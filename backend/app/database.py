from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from .utils.logger import logger

engine = None
SessionLocal = None
Base = declarative_base()

shared_engine = None
SharedSessionLocal = None
_shared_db_available = False
_shared_db_error = None

def init_engine():
    """Initialize database engine"""
    global engine, SessionLocal
    from .config import settings
    
    if settings.IS_FIRST_RUN:
        return None
    
    engine = create_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,
        pool_size=20,
        max_overflow=200,
        pool_recycle=3600,
        pool_timeout=10,
        connect_args={
            "connect_timeout": 10,
            "options": "-c statement_timeout=300000"
        }
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return engine

def init_shared_engine():
    """Initialize shared tag database engine if enabled"""
    global shared_engine, SharedSessionLocal, _shared_db_available, _shared_db_error
    from .config import settings
    
    if not settings.SHARED_TAGS_ENABLED:
        _shared_db_available = False
        return None
    
    try:
        shared_engine = create_engine(
            settings.SHARED_TAG_DATABASE_URL,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            pool_recycle=3600,
            connect_args={
                "connect_timeout": 5,
                "options": "-c statement_timeout=30000"
            }
        )
        SharedSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=shared_engine)
        
        # Test connection
        with shared_engine.connect() as conn:
            from sqlalchemy import text
            conn.execute(text("SELECT 1"))
        
        _shared_db_available = True
        _shared_db_error = None
        logger.info(f"Shared tag database connected: {settings.SHARED_TAG_DB_HOST}:{settings.SHARED_TAG_DB_PORT}/{settings.SHARED_TAG_DB_NAME}")
        return shared_engine
        
    except Exception as e:
        _shared_db_available = False
        _shared_db_error = str(e)
        logger.warning(f"Warning: Could not connect to shared tag database: {e}")
        logger.warning("Continuing with local tags only...")
        return None

def is_shared_db_available() -> bool:
    """Check if shared database is currently available"""
    return _shared_db_available

def get_shared_db_error() -> str:
    """Get the last error message from shared DB connection attempt"""
    return _shared_db_error

def reconnect_shared_db():
    """Attempt to reconnect to the shared database"""
    global shared_engine, SharedSessionLocal, _shared_db_available
    
    # Dispose old engine if exists
    if shared_engine:
        try:
            shared_engine.dispose()
        except Exception:
            pass
    
    shared_engine = None
    SharedSessionLocal = None
    _shared_db_available = False
    
    return init_shared_engine()

def get_db():
    """Get database session"""
    global SessionLocal
    
    if SessionLocal is None:
        init_engine()
    
    if SessionLocal is None:
        raise RuntimeError("Database not initialized. Please complete onboarding first.")
    
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_shared_db():
    """Get shared database session (yields None if not available)"""
    global SharedSessionLocal, _shared_db_available

    if not _shared_db_available or SharedSessionLocal is None:
        yield None
        return

    db = SharedSessionLocal()
    try:
        yield db
    finally:
        try:
            db.close()
        except Exception:
            pass


def assert_test_db():
    from .config import settings
    if not settings.IS_TEST_ENV:
        raise RuntimeError(
            f"assert_test_db() called but VIOLET_ENV={settings.VIOLET_ENV!r}, "
            f"expected 'test'. Refusing to continue."
        )
    db_name = settings.DB_NAME
    if db_name == "blombooru":
        raise RuntimeError(
            "assert_test_db() failed: DB_NAME is 'blombooru' (production default). "
            "Set TEST_DATABASE_URL or POSTGRES_DB to a test-specific name."
        )

def init_db():
    """Initialize database schema"""
    global engine
    
    if engine is None:
        init_engine()
    
    from . import models
    
    Base.metadata.create_all(bind=engine)
    
    check_and_migrate_schema(engine)
    init_shared_db()

def init_shared_db():
    """Initialize shared tag database schema if enabled"""
    global shared_engine, _shared_db_available
    
    from .config import settings
    
    if not settings.SHARED_TAGS_ENABLED:
        return
    
    if shared_engine is None:
        init_shared_engine()
    
    if shared_engine is None or not _shared_db_available:
        return
    
    try:
        from .shared_tag_models import SharedBase
        SharedBase.metadata.create_all(bind=shared_engine)
        logger.info("Shared tag database schema initialized")
    except Exception as e:
        logger.warning(f"Warning: Could not initialize shared tag database schema: {e}")

def check_and_migrate_schema(engine):
    """Run schema migrations"""
    from sqlalchemy import inspect, text
    
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    
    if 'blombooru_media' not in tables:
        return
    
    migrations = [
        migrate_add_parent_id,
        migrate_add_share_language,
        migrate_add_description,
        migrate_add_scan_jobs_table,
        migrate_add_media_tags_provenance,
        migrate_add_tag_translations_table,
        migrate_add_scan_job_media_table,
        migrate_add_ai_tag_jobs_table,
        migrate_add_tag_translation_jobs_table,
        migrate_audit_proper_noun_translations,
        migrate_add_scan_job_icloud_stats,
        migrate_add_content_classification,
        migrate_add_classification_force_reclassify,
        migrate_add_entity_metadata_tables,
        migrate_add_external_tag_category_lookup_cache,
        migrate_add_pixiv_tag_taxonomy_alias_kb,
        migrate_add_source_metadata_name_registry,
        migrate_add_source_name_candidate_extraction,
        migrate_add_source_concept_resolver_core,
        migrate_add_source_concept_fallback_search_index,
        migrate_add_dynamic_library_sync_tables,
    ]
    
    for migration in migrations:
        migration(engine, inspector)


def migrate_add_parent_id(engine, inspector):
    """Add parent_id column and index to media table"""
    from sqlalchemy import text
    
    columns = [c['name'] for c in inspector.get_columns('blombooru_media')]
    
    if 'parent_id' in columns:
        return
    
    logger.info("Adding parent_id column to blombooru_media...")
    is_sqlite = engine.dialect.name == 'sqlite'
    
    with engine.connect() as conn:
        if is_sqlite:
            conn.execute(text(
                "ALTER TABLE blombooru_media ADD COLUMN parent_id INTEGER"
            ))
        else:
            conn.execute(text(
                "ALTER TABLE blombooru_media ADD COLUMN parent_id INTEGER "
                "REFERENCES blombooru_media(id) ON DELETE SET NULL"
            ))
        
        conn.execute(text(
            "CREATE INDEX ix_blombooru_media_parent_id ON blombooru_media(parent_id)"
        ))
        conn.commit()

def migrate_add_share_language(engine, inspector):
    """Add share_language column to media table"""
    from sqlalchemy import text
    
    columns = [c['name'] for c in inspector.get_columns('blombooru_media')]
    
    if 'share_language' in columns:
        return
    
    logger.info("Adding share_language column to blombooru_media...")
    
    with engine.connect() as conn:
        conn.execute(text(
            "ALTER TABLE blombooru_media ADD COLUMN share_language VARCHAR(10)"
        ))
        conn.commit()

def migrate_add_description(engine, inspector):
    """Add description column to media table"""
    from sqlalchemy import text
    
    columns = [c['name'] for c in inspector.get_columns('blombooru_media')]
    
    if 'description' in columns:
        return
    
    logger.info("Adding description column to blombooru_media...")
    
    with engine.connect() as conn:
        conn.execute(text(
            "ALTER TABLE blombooru_media ADD COLUMN description TEXT"
        ))
        conn.commit()


def migrate_add_media_tags_provenance(engine, inspector):
    """Add provenance columns (source, confidence, is_locked, is_suggestion,
    created_at, updated_at) to blombooru_media_tags for tag metadata tracking.

    Idempotent: safe to run repeatedly. Columns are added only if missing;
    indexes are ensured regardless of whether columns already existed (handles
    the fresh-install case where create_all() adds columns but not named indexes).
    """
    from sqlalchemy import text

    tables = inspector.get_table_names()
    if 'blombooru_media_tags' not in tables:
        return

    columns = [c['name'] for c in inspector.get_columns('blombooru_media_tags')]

    columns_added = False
    if 'source' not in columns:
        logger.info("Adding provenance columns to blombooru_media_tags...")
        with engine.connect() as conn:
            conn.execute(text(
                "ALTER TABLE blombooru_media_tags "
                "ADD COLUMN source VARCHAR(50) NOT NULL DEFAULT 'manual'"
            ))
            conn.execute(text(
                "ALTER TABLE blombooru_media_tags "
                "ADD COLUMN confidence FLOAT"
            ))
            conn.execute(text(
                "ALTER TABLE blombooru_media_tags "
                "ADD COLUMN is_locked BOOLEAN NOT NULL DEFAULT TRUE"
            ))
            conn.execute(text(
                "ALTER TABLE blombooru_media_tags "
                "ADD COLUMN is_suggestion BOOLEAN NOT NULL DEFAULT FALSE"
            ))
            conn.execute(text(
                "ALTER TABLE blombooru_media_tags "
                "ADD COLUMN created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()"
            ))
            conn.execute(text(
                "ALTER TABLE blombooru_media_tags "
                "ADD COLUMN updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()"
            ))
            conn.commit()
        columns_added = True

    # Backfill existing rows (idempotent — only touches rows still at defaults)
    if columns_added:
        with engine.connect() as conn:
            conn.execute(text(
                "UPDATE blombooru_media_tags SET "
                "confidence = 1.0 "
                "WHERE confidence IS NULL"
            ))
            conn.commit()

    # Ensure indexes exist regardless of whether columns were just created or
    # already present (fixes fresh-install where create_all adds columns but
    # not these named indexes).
    with engine.connect() as conn:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_blombooru_media_tags_source "
            "ON blombooru_media_tags(source)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_blombooru_media_tags_is_suggestion "
            "ON blombooru_media_tags(is_suggestion)"
        ))
        conn.commit()

    if columns_added:
        logger.info("blombooru_media_tags provenance columns added successfully.")
    else:
        logger.debug("blombooru_media_tags provenance indexes verified.")


def migrate_add_scan_jobs_table(engine, inspector):
    """Create blombooru_scan_jobs table for scan job tracking"""
    from sqlalchemy import text

    tables = inspector.get_table_names()
    if 'blombooru_scan_jobs' in tables:
        return

    logger.info("Creating blombooru_scan_jobs table...")

    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE blombooru_scan_jobs (
                id SERIAL PRIMARY KEY,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                paths_json TEXT NOT NULL,
                dry_run BOOLEAN NOT NULL DEFAULT FALSE,
                max_files INTEGER,
                started_at TIMESTAMP WITH TIME ZONE,
                finished_at TIMESTAMP WITH TIME ZONE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                total_seen INTEGER NOT NULL DEFAULT 0,
                processed INTEGER NOT NULL DEFAULT 0,
                imported INTEGER NOT NULL DEFAULT 0,
                skipped_duplicate INTEGER NOT NULL DEFAULT 0,
                skipped_unsupported INTEGER NOT NULL DEFAULT 0,
                skipped_limit INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                limit_reached BOOLEAN NOT NULL DEFAULT FALSE,
                failed_files_json TEXT,
                error_message TEXT
            )
        """))
        conn.execute(text(
            "CREATE INDEX ix_blombooru_scan_jobs_status ON blombooru_scan_jobs(status)"
        ))
        conn.execute(text(
            "CREATE INDEX ix_blombooru_scan_jobs_created_at ON blombooru_scan_jobs(created_at)"
        ))
        conn.commit()


def migrate_add_tag_translations_table(engine, inspector):
    """Create blombooru_tag_translations table for tag localization cache"""
    from sqlalchemy import text

    tables = inspector.get_table_names()
    if 'blombooru_tag_translations' in tables:
        return

    logger.info("Creating blombooru_tag_translations table...")

    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE blombooru_tag_translations (
                id SERIAL PRIMARY KEY,
                tag_id INTEGER REFERENCES blombooru_tags(id) ON DELETE CASCADE,
                canonical_name VARCHAR(255) NOT NULL,
                language VARCHAR(10) NOT NULL DEFAULT 'zh-CN',
                display_name VARCHAR(500) NOT NULL,
                aliases_json TEXT,
                category VARCHAR(50),
                source VARCHAR(50) NOT NULL DEFAULT 'static',
                status VARCHAR(50) NOT NULL DEFAULT 'translated',
                confidence FLOAT,
                needs_review BOOLEAN NOT NULL DEFAULT FALSE,
                provider VARCHAR(100),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                CONSTRAINT uq_tag_translation_canonical_lang UNIQUE (canonical_name, language)
            )
        """))
        conn.execute(text(
            "CREATE INDEX ix_blombooru_tag_translations_tag_id "
            "ON blombooru_tag_translations(tag_id)"
        ))
        conn.execute(text(
            "CREATE INDEX ix_blombooru_tag_translations_canonical_name "
            "ON blombooru_tag_translations(canonical_name)"
        ))
        conn.execute(text(
            "CREATE INDEX ix_blombooru_tag_translations_language "
            "ON blombooru_tag_translations(language)"
        ))
        conn.commit()


def migrate_add_scan_job_media_table(engine, inspector):
    """Create blombooru_scan_job_media table for tracking imported media per scan job"""
    from sqlalchemy import text

    tables = inspector.get_table_names()
    if 'blombooru_scan_job_media' in tables:
        return

    logger.info("Creating blombooru_scan_job_media table...")

    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE blombooru_scan_job_media (
                id SERIAL PRIMARY KEY,
                scan_job_id INTEGER NOT NULL REFERENCES blombooru_scan_jobs(id) ON DELETE CASCADE,
                media_id INTEGER NOT NULL REFERENCES blombooru_media(id) ON DELETE CASCADE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """))
        conn.execute(text(
            "CREATE INDEX ix_blombooru_scan_job_media_scan_job_id "
            "ON blombooru_scan_job_media(scan_job_id)"
        ))
        conn.execute(text(
            "CREATE INDEX ix_blombooru_scan_job_media_media_id "
            "ON blombooru_scan_job_media(media_id)"
        ))
        conn.commit()


def migrate_add_ai_tag_jobs_table(engine, inspector):
    """Create blombooru_ai_tag_jobs table for AI tagging job tracking"""
    from sqlalchemy import text

    tables = inspector.get_table_names()
    if 'blombooru_ai_tag_jobs' in tables:
        return

    logger.info("Creating blombooru_ai_tag_jobs table...")

    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE blombooru_ai_tag_jobs (
                id SERIAL PRIMARY KEY,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                trigger_source VARCHAR(20) NOT NULL DEFAULT 'manual',
                scan_job_id INTEGER REFERENCES blombooru_scan_jobs(id) ON DELETE SET NULL,
                media_ids_json TEXT,
                max_items INTEGER NOT NULL DEFAULT 10,
                dry_run BOOLEAN NOT NULL DEFAULT FALSE,
                only_without_ai_tags BOOLEAN NOT NULL DEFAULT TRUE,
                force_suggestions BOOLEAN NOT NULL DEFAULT FALSE,
                processed INTEGER NOT NULL DEFAULT 0,
                tags_added INTEGER NOT NULL DEFAULT 0,
                suggestions_added INTEGER NOT NULL DEFAULT 0,
                skipped_locked INTEGER NOT NULL DEFAULT 0,
                ignored_low_confidence INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                failed_items_json TEXT,
                error_message TEXT,
                localization_status VARCHAR(50),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                started_at TIMESTAMP WITH TIME ZONE,
                finished_at TIMESTAMP WITH TIME ZONE,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """))
        conn.execute(text(
            "CREATE INDEX ix_blombooru_ai_tag_jobs_status "
            "ON blombooru_ai_tag_jobs(status)"
        ))
        conn.execute(text(
            "CREATE INDEX ix_blombooru_ai_tag_jobs_created_at "
            "ON blombooru_ai_tag_jobs(created_at)"
        ))
        conn.execute(text(
            "CREATE INDEX ix_blombooru_ai_tag_jobs_scan_job_id "
            "ON blombooru_ai_tag_jobs(scan_job_id)"
        ))
        conn.commit()


def migrate_add_tag_translation_jobs_table(engine, inspector):
    """Create blombooru_tag_translation_jobs table for background translation tracking"""
    from sqlalchemy import text

    tables = inspector.get_table_names()
    if 'blombooru_tag_translation_jobs' in tables:
        return

    logger.info("Creating blombooru_tag_translation_jobs table...")

    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE blombooru_tag_translation_jobs (
                id SERIAL PRIMARY KEY,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                source VARCHAR(20) NOT NULL DEFAULT 'background',
                language VARCHAR(10) NOT NULL DEFAULT 'zh-CN',
                category VARCHAR(50),
                batch_size INTEGER NOT NULL DEFAULT 100,
                max_per_run INTEGER NOT NULL DEFAULT 500,
                processed INTEGER NOT NULL DEFAULT 0,
                translated INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                skipped INTEGER NOT NULL DEFAULT 0,
                remaining_before INTEGER NOT NULL DEFAULT 0,
                remaining_after INTEGER,
                last_error TEXT,
                error_message TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                started_at TIMESTAMP WITH TIME ZONE,
                finished_at TIMESTAMP WITH TIME ZONE,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """))
        conn.execute(text(
            "CREATE INDEX ix_blombooru_tag_translation_jobs_status "
            "ON blombooru_tag_translation_jobs(status)"
        ))
        conn.execute(text(
            "CREATE INDEX ix_blombooru_tag_translation_jobs_created_at "
            "ON blombooru_tag_translation_jobs(created_at)"
        ))
        conn.commit()


def migrate_audit_proper_noun_translations(engine, inspector):
    """Mark LLM-generated proper-noun translations as needs_review (Phase 2.3e safety audit).

    Proper noun categories (character, copyright, artist) were previously translated by the
    same generic LLM prompt as visual tags. Those translations need human review before they
    should be trusted in search aliases.
    """
    from sqlalchemy import text

    tables = inspector.get_table_names()
    if 'blombooru_tag_translations' not in tables:
        return

    with engine.connect() as conn:
        result = conn.execute(text("""
            UPDATE blombooru_tag_translations
            SET needs_review = true, updated_at = NOW()
            WHERE source = 'llm'
              AND category IN ('character', 'copyright', 'artist')
              AND needs_review = false
              AND status != 'reviewed'
        """))
        count = result.rowcount
        conn.commit()

    if count:
        logger.info("Phase 2.3e audit: marked %d proper-noun LLM translations as needs_review", count)


def migrate_add_scan_job_icloud_stats(engine, inspector):
    """Add iCloud safety columns to blombooru_scan_jobs (Phase 2.4)."""
    from sqlalchemy import text

    tables = inspector.get_table_names()
    if 'blombooru_scan_jobs' not in tables:
        return

    columns = [c['name'] for c in inspector.get_columns('blombooru_scan_jobs')]

    if 'skipped_cloud_placeholder' in columns:
        return

    logger.info("Adding iCloud safety columns to blombooru_scan_jobs...")
    with engine.connect() as conn:
        conn.execute(text(
            "ALTER TABLE blombooru_scan_jobs "
            "ADD COLUMN skipped_cloud_placeholder INTEGER NOT NULL DEFAULT 0"
        ))
        conn.execute(text(
            "ALTER TABLE blombooru_scan_jobs "
            "ADD COLUMN skipped_zero_byte INTEGER NOT NULL DEFAULT 0"
        ))
        conn.execute(text(
            "ALTER TABLE blombooru_scan_jobs "
            "ADD COLUMN skipped_timeout INTEGER NOT NULL DEFAULT 0"
        ))
        conn.execute(text(
            "ALTER TABLE blombooru_scan_jobs "
            "ADD COLUMN skipped_unreadable INTEGER NOT NULL DEFAULT 0"
        ))
        conn.execute(text(
            "ALTER TABLE blombooru_scan_jobs "
            "ADD COLUMN skipped_hidden INTEGER NOT NULL DEFAULT 0"
        ))
        conn.execute(text(
            "ALTER TABLE blombooru_scan_jobs "
            "ADD COLUMN skipped_too_large INTEGER NOT NULL DEFAULT 0"
        ))
        conn.execute(text(
            "ALTER TABLE blombooru_scan_jobs "
            "ADD COLUMN hydrated_only BOOLEAN NOT NULL DEFAULT TRUE"
        ))
        conn.execute(text(
            "ALTER TABLE blombooru_scan_jobs "
            "ADD COLUMN is_preflight BOOLEAN NOT NULL DEFAULT FALSE"
        ))
        conn.commit()


def migrate_add_content_classification(engine, inspector):
    """Add content classification columns to blombooru_media and create
    blombooru_classification_jobs table (Phase 3)."""
    from sqlalchemy import text

    columns = [c['name'] for c in inspector.get_columns('blombooru_media')]

    if 'content_class' not in columns:
        logger.info("Adding content classification columns to blombooru_media...")
        with engine.connect() as conn:
            conn.execute(text(
                "ALTER TABLE blombooru_media "
                "ADD COLUMN content_class VARCHAR(20)"
            ))
            conn.execute(text(
                "ALTER TABLE blombooru_media "
                "ADD COLUMN content_class_confidence FLOAT"
            ))
            conn.execute(text(
                "ALTER TABLE blombooru_media "
                "ADD COLUMN content_class_source VARCHAR(50)"
            ))
            conn.execute(text(
                "ALTER TABLE blombooru_media "
                "ADD COLUMN content_class_model VARCHAR(100)"
            ))
            conn.execute(text(
                "ALTER TABLE blombooru_media "
                "ADD COLUMN content_class_locked BOOLEAN NOT NULL DEFAULT FALSE"
            ))
            conn.execute(text(
                "ALTER TABLE blombooru_media "
                "ADD COLUMN content_class_reviewed BOOLEAN NOT NULL DEFAULT FALSE"
            ))
            conn.execute(text(
                "CREATE INDEX ix_blombooru_media_content_class "
                "ON blombooru_media(content_class)"
            ))
            conn.commit()

    tables = inspector.get_table_names()
    if 'blombooru_classification_jobs' not in tables:
        logger.info("Creating blombooru_classification_jobs table...")
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE blombooru_classification_jobs (
                    id SERIAL PRIMARY KEY,
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    trigger_source VARCHAR(20) NOT NULL DEFAULT 'manual',
                    scan_job_id INTEGER REFERENCES blombooru_scan_jobs(id) ON DELETE SET NULL,
                    media_ids_json TEXT,
                    max_items INTEGER NOT NULL DEFAULT 100,
                    only_unclassified BOOLEAN NOT NULL DEFAULT TRUE,
                    processed INTEGER NOT NULL DEFAULT 0,
                    classified_anime INTEGER NOT NULL DEFAULT 0,
                    classified_non_anime INTEGER NOT NULL DEFAULT 0,
                    classified_unknown INTEGER NOT NULL DEFAULT 0,
                    failed INTEGER NOT NULL DEFAULT 0,
                    failed_items_json TEXT,
                    error_message TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    started_at TIMESTAMP WITH TIME ZONE,
                    finished_at TIMESTAMP WITH TIME ZONE,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """))
            conn.execute(text(
                "CREATE INDEX ix_blombooru_classification_jobs_status "
                "ON blombooru_classification_jobs(status)"
            ))
            conn.execute(text(
                "CREATE INDEX ix_blombooru_classification_jobs_created_at "
                "ON blombooru_classification_jobs(created_at)"
            ))
            conn.execute(text(
                "CREATE INDEX ix_blombooru_classification_jobs_scan_job_id "
                "ON blombooru_classification_jobs(scan_job_id)"
            ))
            conn.commit()


def migrate_add_classification_force_reclassify(engine, inspector):
    """Add force_reclassify column to blombooru_classification_jobs."""
    from sqlalchemy import text

    tables = inspector.get_table_names()
    if 'blombooru_classification_jobs' not in tables:
        return

    columns = [c['name'] for c in inspector.get_columns('blombooru_classification_jobs')]
    if 'force_reclassify' not in columns:
        logger.info("Adding force_reclassify column to blombooru_classification_jobs...")
        with engine.connect() as conn:
            conn.execute(text(
                "ALTER TABLE blombooru_classification_jobs "
                "ADD COLUMN force_reclassify BOOLEAN NOT NULL DEFAULT FALSE"
            ))
            conn.commit()


def migrate_add_entity_metadata_tables(engine, inspector):
    """Create Phase 4.1 entity metadata foundation tables.

    This migration is additive only: it creates new tables for entity records,
    aliases, identities, provenance/evidence, media candidates, confirmed
    assignments, translations, and inactive provider/cache policy placeholders.
    It does not backfill from tags or run enrichment.
    """
    from sqlalchemy import text

    tables = set(inspector.get_table_names())
    is_sqlite = engine.dialect.name == 'sqlite'
    pk_type = 'INTEGER PRIMARY KEY AUTOINCREMENT' if is_sqlite else 'SERIAL PRIMARY KEY'
    now_expr = 'CURRENT_TIMESTAMP' if is_sqlite else 'NOW()'
    json_type = 'TEXT' if is_sqlite else 'JSONB'

    with engine.connect() as conn:
        if 'blombooru_entities' not in tables:
            logger.info("Creating blombooru_entities table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_entities (
                    id {pk_type},
                    type VARCHAR(50) NOT NULL,
                    canonical_name VARCHAR(500) NOT NULL,
                    normalized_key VARCHAR(500) NOT NULL,
                    slug VARCHAR(500),
                    status VARCHAR(50) NOT NULL DEFAULT 'active',
                    description TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_entity_type_normalized_key UNIQUE (type, normalized_key)
                )
            """))

        if 'blombooru_entity_aliases' not in tables:
            logger.info("Creating blombooru_entity_aliases table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_entity_aliases (
                    id {pk_type},
                    entity_id INTEGER NOT NULL REFERENCES blombooru_entities(id) ON DELETE CASCADE,
                    alias VARCHAR(500) NOT NULL,
                    normalized_alias VARCHAR(500) NOT NULL,
                    language VARCHAR(20),
                    alias_type VARCHAR(50) NOT NULL DEFAULT 'search',
                    source VARCHAR(50) NOT NULL DEFAULT 'manual',
                    confidence FLOAT,
                    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
                    needs_review BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_entity_alias_entity_normalized UNIQUE (entity_id, normalized_alias)
                )
            """))

        if 'blombooru_entity_external_identities' not in tables:
            logger.info("Creating blombooru_entity_external_identities table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_entity_external_identities (
                    id {pk_type},
                    entity_id INTEGER NOT NULL REFERENCES blombooru_entities(id) ON DELETE CASCADE,
                    provider VARCHAR(100) NOT NULL,
                    external_id VARCHAR(255) NOT NULL,
                    external_url VARCHAR(1000),
                    identity_status VARCHAR(50) NOT NULL DEFAULT 'candidate',
                    confidence FLOAT,
                    last_verified_at TIMESTAMP WITH TIME ZONE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_entity_external_provider_id UNIQUE (provider, external_id)
                )
            """))

        if 'blombooru_entity_evidence' not in tables:
            logger.info("Creating blombooru_entity_evidence table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_entity_evidence (
                    id {pk_type},
                    provider VARCHAR(100),
                    source_type VARCHAR(100) NOT NULL DEFAULT 'manual',
                    evidence_type VARCHAR(50) NOT NULL DEFAULT 'manual',
                    media_id INTEGER REFERENCES blombooru_media(id) ON DELETE SET NULL,
                    tag_id INTEGER REFERENCES blombooru_tags(id) ON DELETE SET NULL,
                    entity_id INTEGER REFERENCES blombooru_entities(id) ON DELETE SET NULL,
                    query_hash VARCHAR(128),
                    payload_ref VARCHAR(500),
                    score FLOAT,
                    summary TEXT,
                    privacy_redacted BOOLEAN NOT NULL DEFAULT TRUE,
                    observed_at TIMESTAMP WITH TIME ZONE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr}
                )
            """))

        if 'blombooru_media_entity_candidates' not in tables:
            logger.info("Creating blombooru_media_entity_candidates table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_media_entity_candidates (
                    id {pk_type},
                    media_id INTEGER NOT NULL REFERENCES blombooru_media(id) ON DELETE CASCADE,
                    entity_id INTEGER REFERENCES blombooru_entities(id) ON DELETE SET NULL,
                    entity_type VARCHAR(50) NOT NULL,
                    label VARCHAR(500),
                    candidate_name VARCHAR(500) NOT NULL,
                    score FLOAT,
                    status VARCHAR(50) NOT NULL DEFAULT 'suggested',
                    generator VARCHAR(50) NOT NULL DEFAULT 'manual',
                    evidence_id INTEGER REFERENCES blombooru_entity_evidence(id) ON DELETE SET NULL,
                    review_reason TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr}
                )
            """))

        if 'blombooru_media_entity_assignments' not in tables:
            logger.info("Creating blombooru_media_entity_assignments table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_media_entity_assignments (
                    id {pk_type},
                    media_id INTEGER NOT NULL REFERENCES blombooru_media(id) ON DELETE CASCADE,
                    entity_id INTEGER NOT NULL REFERENCES blombooru_entities(id) ON DELETE CASCADE,
                    role VARCHAR(50) NOT NULL,
                    confidence FLOAT,
                    review_status VARCHAR(50) NOT NULL DEFAULT 'needs_review',
                    source VARCHAR(50) NOT NULL DEFAULT 'manual',
                    locked BOOLEAN NOT NULL DEFAULT FALSE,
                    created_from_candidate_id INTEGER REFERENCES blombooru_media_entity_candidates(id) ON DELETE SET NULL,
                    evidence_id INTEGER REFERENCES blombooru_entity_evidence(id) ON DELETE SET NULL,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_media_entity_assignment_role UNIQUE (media_id, entity_id, role)
                )
            """))

        if 'blombooru_entity_translations' not in tables:
            logger.info("Creating blombooru_entity_translations table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_entity_translations (
                    id {pk_type},
                    entity_id INTEGER NOT NULL REFERENCES blombooru_entities(id) ON DELETE CASCADE,
                    language VARCHAR(20) NOT NULL DEFAULT 'zh-CN',
                    display_name VARCHAR(500) NOT NULL,
                    source VARCHAR(50) NOT NULL DEFAULT 'manual',
                    status VARCHAR(50) NOT NULL DEFAULT 'needs_review',
                    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_entity_translation_display UNIQUE (entity_id, language, display_name)
                )
            """))

        if 'blombooru_external_sources' not in tables:
            logger.info("Creating blombooru_external_sources table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_external_sources (
                    id {pk_type},
                    provider VARCHAR(100) NOT NULL,
                    enabled BOOLEAN NOT NULL DEFAULT FALSE,
                    auth_mode VARCHAR(50) NOT NULL DEFAULT 'none',
                    base_url VARCHAR(1000),
                    rate_limit_policy {json_type},
                    privacy_policy {json_type},
                    terms_url VARCHAR(1000),
                    notes TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_external_sources_provider UNIQUE (provider)
                )
            """))

        if 'blombooru_provider_cache' not in tables:
            logger.info("Creating blombooru_provider_cache table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_provider_cache (
                    id {pk_type},
                    provider VARCHAR(100) NOT NULL,
                    query_hash VARCHAR(128) NOT NULL,
                    query_type VARCHAR(100) NOT NULL,
                    request_shape_redacted {json_type},
                    response_status VARCHAR(100) NOT NULL,
                    response_json_redacted {json_type},
                    error_class VARCHAR(100),
                    fetched_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT {now_expr},
                    expires_at TIMESTAMP WITH TIME ZONE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_provider_cache_query UNIQUE (provider, query_hash, query_type)
                )
            """))

        if 'blombooru_negative_lookup_cache' not in tables:
            logger.info("Creating blombooru_negative_lookup_cache table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_negative_lookup_cache (
                    id {pk_type},
                    provider VARCHAR(100) NOT NULL,
                    query_hash VARCHAR(128) NOT NULL,
                    query_type VARCHAR(100) NOT NULL,
                    reason VARCHAR(255) NOT NULL,
                    expires_at TIMESTAMP WITH TIME ZONE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_negative_lookup_cache_query UNIQUE (provider, query_hash, query_type)
                )
            """))

        index_statements = [
            "CREATE INDEX IF NOT EXISTS ix_blombooru_entities_type_status ON blombooru_entities(type, status)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_entities_normalized_key ON blombooru_entities(normalized_key)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_entities_slug ON blombooru_entities(slug)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_entity_aliases_entity_id ON blombooru_entity_aliases(entity_id)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_entity_aliases_normalized_alias ON blombooru_entity_aliases(normalized_alias)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_entity_aliases_language ON blombooru_entity_aliases(language)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_entity_aliases_source ON blombooru_entity_aliases(source)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_entity_aliases_needs_review ON blombooru_entity_aliases(needs_review)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_entity_external_entity_provider ON blombooru_entity_external_identities(entity_id, provider)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_entity_external_status ON blombooru_entity_external_identities(identity_status)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_entity_external_provider ON blombooru_entity_external_identities(provider)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_entity_evidence_source_evidence_type ON blombooru_entity_evidence(source_type, evidence_type)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_entity_evidence_provider_query ON blombooru_entity_evidence(provider, query_hash)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_entity_evidence_media_type ON blombooru_entity_evidence(media_id, evidence_type)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_entity_evidence_tag_id ON blombooru_entity_evidence(tag_id)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_entity_evidence_entity_id ON blombooru_entity_evidence(entity_id)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_entity_evidence_query_hash ON blombooru_entity_evidence(query_hash)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_media_entity_candidates_media_status ON blombooru_media_entity_candidates(media_id, status)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_media_entity_candidates_entity_type_status ON blombooru_media_entity_candidates(entity_type, status)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_media_entity_candidates_generator ON blombooru_media_entity_candidates(generator)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_media_entity_candidates_entity_id ON blombooru_media_entity_candidates(entity_id)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_media_entity_candidates_evidence_id ON blombooru_media_entity_candidates(evidence_id)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_media_entity_assignments_media_review ON blombooru_media_entity_assignments(media_id, review_status)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_media_entity_assignments_entity_role ON blombooru_media_entity_assignments(entity_id, role)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_media_entity_assignments_source ON blombooru_media_entity_assignments(source)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_media_entity_assignments_candidate ON blombooru_media_entity_assignments(created_from_candidate_id)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_media_entity_assignments_evidence ON blombooru_media_entity_assignments(evidence_id)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_entity_translations_language_status ON blombooru_entity_translations(language, status)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_entity_translations_source ON blombooru_entity_translations(source)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_entity_translations_entity_id ON blombooru_entity_translations(entity_id)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_external_sources_enabled ON blombooru_external_sources(enabled)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_external_sources_provider ON blombooru_external_sources(provider)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_provider_cache_provider_fetched ON blombooru_provider_cache(provider, fetched_at)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_provider_cache_expires_at ON blombooru_provider_cache(expires_at)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_provider_cache_error_class ON blombooru_provider_cache(error_class)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_negative_lookup_cache_expires_at ON blombooru_negative_lookup_cache(expires_at)",
            "CREATE INDEX IF NOT EXISTS ix_blombooru_negative_lookup_cache_provider ON blombooru_negative_lookup_cache(provider)",
        ]
        for statement in index_statements:
            conn.execute(text(statement))
        conn.commit()


def migrate_add_source_concept_resolver_core(engine, inspector):
    """Create Phase 4.5-SC1 source-layer SourceConcept resolver tables.

    This migration is additive only. It stores unconfirmed source-layer
    concepts, aliases, evidence, signal links, run ledger rows, and a search
    preview index. It does not create or mutate Entity, EntityAlias,
    EntityEvidence, MediaEntityCandidate, MediaEntityAssignment, media_tags,
    TagTranslation, ProviderCache, NegativeLookupCache, or confirmed
    assignments.
    """
    from sqlalchemy import text

    tables = set(inspector.get_table_names())
    is_sqlite = engine.dialect.name == 'sqlite'
    pk_type = 'INTEGER PRIMARY KEY AUTOINCREMENT' if is_sqlite else 'SERIAL PRIMARY KEY'
    now_expr = 'CURRENT_TIMESTAMP' if is_sqlite else 'NOW()'
    json_type = 'JSON' if not is_sqlite else 'JSON'

    with engine.connect() as conn:
        if 'blombooru_source_concept_resolution_runs' not in tables:
            logger.info("Creating blombooru_source_concept_resolution_runs table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_source_concept_resolution_runs (
                    id {pk_type},
                    run_id VARCHAR(255) NOT NULL,
                    run_label VARCHAR(255),
                    scope VARCHAR(100) NOT NULL DEFAULT 'source_concept_core',
                    resolver_version VARCHAR(100) NOT NULL,
                    mode VARCHAR(100) NOT NULL DEFAULT 'dry_run',
                    status VARCHAR(50) NOT NULL DEFAULT 'running',
                    input_signal_counts_json {json_type},
                    linked_counts_json {json_type},
                    concept_counts_json {json_type},
                    review_counts_json {json_type},
                    no_truth_write_proof_json {json_type},
                    summary_json {json_type},
                    started_at TIMESTAMP WITH TIME ZONE,
                    finished_at TIMESTAMP WITH TIME ZONE,
                    runtime_seconds FLOAT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_source_concept_resolution_run_id UNIQUE (run_id)
                )
            """))

        if 'blombooru_source_concept_signals' not in tables:
            logger.info("Creating blombooru_source_concept_signals table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_source_concept_signals (
                    id {pk_type},
                    resolution_run_id INTEGER REFERENCES blombooru_source_concept_resolution_runs(id) ON DELETE SET NULL,
                    signal_key VARCHAR(900) NOT NULL,
                    origin_type VARCHAR(100) NOT NULL,
                    origin_table VARCHAR(255),
                    origin_id VARCHAR(500),
                    provider VARCHAR(100),
                    media_id INTEGER REFERENCES blombooru_media(id) ON DELETE SET NULL,
                    source_metadata_record_id INTEGER REFERENCES blombooru_source_metadata_records(id) ON DELETE SET NULL,
                    source_record_id VARCHAR(500),
                    raw_value VARCHAR(1000) NOT NULL,
                    display_value VARCHAR(1000) NOT NULL,
                    normalized_key VARCHAR(500) NOT NULL,
                    canonical_key VARCHAR(500),
                    role_hint VARCHAR(100) NOT NULL DEFAULT 'unknown',
                    work_context_key VARCHAR(500),
                    parenthetical_base VARCHAR(500),
                    parenthetical_context VARCHAR(500),
                    source_kind VARCHAR(100),
                    trust_tier VARCHAR(50) NOT NULL DEFAULT 'weak',
                    confidence FLOAT,
                    status VARCHAR(50) NOT NULL DEFAULT 'needs_review',
                    evidence_payload {json_type},
                    source_run_id VARCHAR(255),
                    created_by_run_id VARCHAR(255),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_source_concept_signal_key UNIQUE (signal_key)
                )
            """))

        if 'blombooru_source_concepts' not in tables:
            logger.info("Creating blombooru_source_concepts table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_source_concepts (
                    id {pk_type},
                    concept_key VARCHAR(900) NOT NULL,
                    primary_display_name VARCHAR(1000) NOT NULL,
                    concept_type_hint VARCHAR(100) NOT NULL DEFAULT 'unknown',
                    status VARCHAR(50) NOT NULL DEFAULT 'needs_review',
                    confidence_score FLOAT,
                    evidence_score FLOAT,
                    media_count INTEGER NOT NULL DEFAULT 0,
                    source_count INTEGER NOT NULL DEFAULT 0,
                    created_by_run_id VARCHAR(255),
                    superseded_by_concept_id INTEGER REFERENCES blombooru_source_concepts(id) ON DELETE SET NULL,
                    evidence_summary_json {json_type},
                    lifecycle_payload {json_type},
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_source_concept_key UNIQUE (concept_key)
                )
            """))

        if 'blombooru_source_concept_aliases' not in tables:
            logger.info("Creating blombooru_source_concept_aliases table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_source_concept_aliases (
                    id {pk_type},
                    concept_id INTEGER NOT NULL REFERENCES blombooru_source_concepts(id) ON DELETE CASCADE,
                    alias_value VARCHAR(1000) NOT NULL,
                    alias_key VARCHAR(500) NOT NULL,
                    display_name VARCHAR(1000) NOT NULL,
                    language_hint VARCHAR(50),
                    script_hint VARCHAR(50),
                    alias_role VARCHAR(100) NOT NULL,
                    status VARCHAR(50) NOT NULL DEFAULT 'needs_review',
                    confidence FLOAT,
                    source_signal_id INTEGER REFERENCES blombooru_source_concept_signals(id) ON DELETE SET NULL,
                    evidence_payload {json_type},
                    created_by_run_id VARCHAR(255),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_source_concept_alias_concept_key_role UNIQUE (
                        concept_id,
                        alias_key,
                        alias_role
                    )
                )
            """))

        if 'blombooru_source_concept_evidence' not in tables:
            logger.info("Creating blombooru_source_concept_evidence table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_source_concept_evidence (
                    id {pk_type},
                    concept_id INTEGER NOT NULL REFERENCES blombooru_source_concepts(id) ON DELETE CASCADE,
                    signal_id INTEGER REFERENCES blombooru_source_concept_signals(id) ON DELETE SET NULL,
                    media_id INTEGER REFERENCES blombooru_media(id) ON DELETE SET NULL,
                    source_metadata_record_id INTEGER REFERENCES blombooru_source_metadata_records(id) ON DELETE SET NULL,
                    provider VARCHAR(100),
                    evidence_type VARCHAR(100) NOT NULL,
                    evidence_strength VARCHAR(50) NOT NULL DEFAULT 'weak',
                    payload {json_type},
                    run_id VARCHAR(255),
                    status VARCHAR(50) NOT NULL DEFAULT 'needs_review',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_source_concept_evidence_concept_signal_type UNIQUE (
                        concept_id,
                        signal_id,
                        evidence_type
                    )
                )
            """))

        if 'blombooru_source_concept_signal_links' not in tables:
            logger.info("Creating blombooru_source_concept_signal_links table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_source_concept_signal_links (
                    id {pk_type},
                    signal_id INTEGER NOT NULL REFERENCES blombooru_source_concept_signals(id) ON DELETE CASCADE,
                    concept_id INTEGER NOT NULL REFERENCES blombooru_source_concepts(id) ON DELETE CASCADE,
                    link_status VARCHAR(50) NOT NULL DEFAULT 'needs_review',
                    confidence FLOAT,
                    resolution_reason_code VARCHAR(100),
                    negative_reason_code VARCHAR(100),
                    resolver_version VARCHAR(100) NOT NULL,
                    run_id VARCHAR(255) NOT NULL,
                    evidence_payload {json_type},
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_source_concept_signal_link_run UNIQUE (
                        signal_id,
                        concept_id,
                        run_id
                    )
                )
            """))

        if 'blombooru_source_concept_search_index' not in tables:
            logger.info("Creating blombooru_source_concept_search_index table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_source_concept_search_index (
                    id {pk_type},
                    concept_id INTEGER NOT NULL REFERENCES blombooru_source_concepts(id) ON DELETE CASCADE,
                    search_key VARCHAR(500) NOT NULL,
                    display_name VARCHAR(1000) NOT NULL,
                    alias_role VARCHAR(100) NOT NULL,
                    weight FLOAT NOT NULL DEFAULT 0,
                    status VARCHAR(50) NOT NULL DEFAULT 'needs_review',
                    evidence_refs_json {json_type},
                    run_id VARCHAR(255),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_source_concept_search_index_key_role UNIQUE (
                        concept_id,
                        search_key,
                        alias_role
                    )
                )
            """))

        index_statements = [
            "CREATE INDEX IF NOT EXISTS ix_source_concept_resolution_run_status ON blombooru_source_concept_resolution_runs(status)",
            "CREATE INDEX IF NOT EXISTS ix_source_concept_resolution_run_scope ON blombooru_source_concept_resolution_runs(scope)",
            "CREATE INDEX IF NOT EXISTS ix_source_concept_signal_origin ON blombooru_source_concept_signals(origin_type, origin_id)",
            "CREATE INDEX IF NOT EXISTS ix_source_concept_signal_provider_role ON blombooru_source_concept_signals(provider, role_hint)",
            "CREATE INDEX IF NOT EXISTS ix_source_concept_signal_status_trust ON blombooru_source_concept_signals(status, trust_tier)",
            "CREATE INDEX IF NOT EXISTS ix_source_concept_signal_canonical ON blombooru_source_concept_signals(canonical_key)",
            "CREATE INDEX IF NOT EXISTS ix_source_concept_signal_work_context ON blombooru_source_concept_signals(work_context_key)",
            "CREATE INDEX IF NOT EXISTS ix_source_concept_signal_media ON blombooru_source_concept_signals(media_id)",
            "CREATE INDEX IF NOT EXISTS ix_source_concept_signal_source_record ON blombooru_source_concept_signals(source_metadata_record_id)",
            "CREATE INDEX IF NOT EXISTS ix_source_concept_type_status ON blombooru_source_concepts(concept_type_hint, status)",
            "CREATE INDEX IF NOT EXISTS ix_source_concept_status_confidence ON blombooru_source_concepts(status, confidence_score)",
            "CREATE INDEX IF NOT EXISTS ix_source_concept_superseded_by ON blombooru_source_concepts(superseded_by_concept_id)",
            "CREATE INDEX IF NOT EXISTS ix_source_concept_alias_lookup ON blombooru_source_concept_aliases(alias_key, status)",
            "CREATE INDEX IF NOT EXISTS ix_source_concept_alias_role_status ON blombooru_source_concept_aliases(alias_role, status)",
            "CREATE INDEX IF NOT EXISTS ix_source_concept_alias_signal ON blombooru_source_concept_aliases(source_signal_id)",
            "CREATE INDEX IF NOT EXISTS ix_source_concept_evidence_provider_type ON blombooru_source_concept_evidence(provider, evidence_type)",
            "CREATE INDEX IF NOT EXISTS ix_source_concept_evidence_status_strength ON blombooru_source_concept_evidence(status, evidence_strength)",
            "CREATE INDEX IF NOT EXISTS ix_source_concept_evidence_media ON blombooru_source_concept_evidence(media_id)",
            "CREATE INDEX IF NOT EXISTS ix_source_concept_evidence_source_record ON blombooru_source_concept_evidence(source_metadata_record_id)",
            "CREATE INDEX IF NOT EXISTS ix_source_concept_signal_link_status ON blombooru_source_concept_signal_links(link_status)",
            "CREATE INDEX IF NOT EXISTS ix_source_concept_signal_link_reason ON blombooru_source_concept_signal_links(resolution_reason_code)",
            "CREATE INDEX IF NOT EXISTS ix_source_concept_search_lookup ON blombooru_source_concept_search_index(search_key, status)",
            "CREATE INDEX IF NOT EXISTS ix_source_concept_search_weight ON blombooru_source_concept_search_index(weight)",
        ]
        for statement in index_statements:
            conn.execute(text(statement))
        conn.commit()


def migrate_add_source_concept_fallback_search_index(engine, inspector):
    """Add the indexed SCV2-R2R non-materialized evidence lookup.

    This additive table is SourceConcept/source-layer only. It is not an Entity
    or truth-path table and is populated only by an explicitly approved R2R
    materialization run.
    """
    from sqlalchemy import text

    tables = set(inspector.get_table_names())
    if 'blombooru_source_concept_fallback_search_index' in tables:
        return

    is_sqlite = engine.dialect.name == 'sqlite'
    pk_type = 'INTEGER PRIMARY KEY AUTOINCREMENT' if is_sqlite else 'SERIAL PRIMARY KEY'
    now_expr = 'CURRENT_TIMESTAMP' if is_sqlite else 'NOW()'
    json_type = 'JSON'
    with engine.connect() as conn:
        conn.execute(text(f"""
            CREATE TABLE blombooru_source_concept_fallback_search_index (
                id {pk_type},
                alias_key VARCHAR(500) NOT NULL,
                media_id INTEGER REFERENCES blombooru_media(id) ON DELETE CASCADE,
                source_signal_id INTEGER NOT NULL REFERENCES blombooru_source_concept_signals(id) ON DELETE CASCADE,
                neighbor_signal_id INTEGER NOT NULL REFERENCES blombooru_source_concept_signals(id) ON DELETE CASCADE,
                pair_id VARCHAR(64) NOT NULL,
                relation VARCHAR(50) NOT NULL,
                overlay_version VARCHAR(100) NOT NULL,
                disposition_version VARCHAR(100) NOT NULL,
                role_hint VARCHAR(100),
                work_context_key VARCHAR(500),
                provenance_payload {json_type},
                status VARCHAR(50) NOT NULL DEFAULT 'active',
                run_id VARCHAR(255) NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                CONSTRAINT uq_source_concept_fallback_search_row UNIQUE (
                    alias_key,
                    media_id,
                    source_signal_id,
                    neighbor_signal_id,
                    pair_id,
                    overlay_version
                )
            )
        """))
        for statement in (
            "CREATE INDEX IF NOT EXISTS ix_source_concept_fallback_search_lookup ON blombooru_source_concept_fallback_search_index(alias_key, status, overlay_version)",
            "CREATE INDEX IF NOT EXISTS ix_source_concept_fallback_search_pair ON blombooru_source_concept_fallback_search_index(pair_id, relation)",
            "CREATE INDEX IF NOT EXISTS ix_source_concept_fallback_search_media ON blombooru_source_concept_fallback_search_index(media_id)",
        ):
            conn.execute(text(statement))
        conn.commit()


def migrate_add_external_tag_category_lookup_cache(engine, inspector):
    """Create the external tag category lookup cache table.

    This table stores reproducible provider category lookup evidence for raw
    tag classification. It is intentionally separate from ProviderCache,
    EntityEvidence, MediaEntityCandidate, confirmed assignments, and media_tags.
    """
    from sqlalchemy import inspect, text

    tables = set(inspector.get_table_names())
    is_sqlite = engine.dialect.name == 'sqlite'
    pk_type = 'INTEGER PRIMARY KEY AUTOINCREMENT' if is_sqlite else 'SERIAL PRIMARY KEY'
    now_expr = 'CURRENT_TIMESTAMP' if is_sqlite else 'NOW()'

    with engine.connect() as conn:
        created_table = False
        if 'blombooru_external_tag_category_lookup_cache' not in tables:
            logger.info("Creating blombooru_external_tag_category_lookup_cache table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_external_tag_category_lookup_cache (
                    id {pk_type},
                    raw_tag VARCHAR(500),
                    normalized_tag VARCHAR(500) NOT NULL,
                    canonical_lookup_key VARCHAR(500),
                    lookup_source VARCHAR(100) NOT NULL,
                    lookup_source_version VARCHAR(100),
                    source_tag_id VARCHAR(255),
                    source_tag_name VARCHAR(500),
                    source_category_raw VARCHAR(100),
                    mapped_candidate_namespace VARCHAR(50),
                    confidence FLOAT,
                    provenance_url_or_key VARCHAR(1000),
                    status VARCHAR(50) NOT NULL DEFAULT 'pending',
                    first_seen_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    last_checked_at TIMESTAMP WITH TIME ZONE,
                    retry_after TIMESTAMP WITH TIME ZONE,
                    expires_at TIMESTAMP WITH TIME ZONE,
                    lookup_error TEXT,
                    manual_override_status VARCHAR(50) NOT NULL DEFAULT 'none',
                    manual_override_value VARCHAR(500),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_external_tag_category_lookup_key UNIQUE (lookup_source, normalized_tag),
                    CONSTRAINT uq_external_tag_category_canonical_lookup_key UNIQUE (lookup_source, canonical_lookup_key)
                )
            """))
            created_table = True

        if conn.dialect.name == 'postgresql':
            conn.execute(text(
                "ALTER TABLE blombooru_external_tag_category_lookup_cache "
                "DROP CONSTRAINT IF EXISTS uq_external_tag_category_source_tag_id"
            ))

        conn_inspector = inspect(conn)
        columns = (
            {
                'canonical_lookup_key',
                'lookup_source_version',
                'retry_after',
                'expires_at',
                'manual_override_value',
            }
            if created_table
            else {
                column['name']
                for column in conn_inspector.get_columns('blombooru_external_tag_category_lookup_cache')
            }
        )
        add_column_statements = {
            'canonical_lookup_key': "ALTER TABLE blombooru_external_tag_category_lookup_cache ADD COLUMN canonical_lookup_key VARCHAR(500)",
            'lookup_source_version': "ALTER TABLE blombooru_external_tag_category_lookup_cache ADD COLUMN lookup_source_version VARCHAR(100)",
            'retry_after': "ALTER TABLE blombooru_external_tag_category_lookup_cache ADD COLUMN retry_after TIMESTAMP WITH TIME ZONE",
            'expires_at': "ALTER TABLE blombooru_external_tag_category_lookup_cache ADD COLUMN expires_at TIMESTAMP WITH TIME ZONE",
            'manual_override_value': "ALTER TABLE blombooru_external_tag_category_lookup_cache ADD COLUMN manual_override_value VARCHAR(500)",
        }
        for column_name, statement in add_column_statements.items():
            if column_name not in columns:
                conn.execute(text(statement))

        index_statements = [
            "CREATE INDEX IF NOT EXISTS ix_external_tag_category_lookup_normalized_tag ON blombooru_external_tag_category_lookup_cache(normalized_tag)",
            "CREATE INDEX IF NOT EXISTS ix_external_tag_category_lookup_source ON blombooru_external_tag_category_lookup_cache(lookup_source)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_external_tag_category_canonical_lookup_key_idx ON blombooru_external_tag_category_lookup_cache(lookup_source, canonical_lookup_key)",
            "CREATE INDEX IF NOT EXISTS ix_external_tag_category_lookup_source_canonical ON blombooru_external_tag_category_lookup_cache(lookup_source, canonical_lookup_key)",
            "CREATE INDEX IF NOT EXISTS ix_external_tag_category_lookup_source_tag_id ON blombooru_external_tag_category_lookup_cache(lookup_source, source_tag_id)",
            "CREATE INDEX IF NOT EXISTS ix_external_tag_category_lookup_source_status ON blombooru_external_tag_category_lookup_cache(lookup_source, status)",
            "CREATE INDEX IF NOT EXISTS ix_external_tag_category_lookup_namespace ON blombooru_external_tag_category_lookup_cache(mapped_candidate_namespace)",
            "CREATE INDEX IF NOT EXISTS ix_external_tag_category_lookup_checked ON blombooru_external_tag_category_lookup_cache(last_checked_at)",
            "CREATE INDEX IF NOT EXISTS ix_external_tag_category_lookup_retry_after ON blombooru_external_tag_category_lookup_cache(retry_after)",
            "CREATE INDEX IF NOT EXISTS ix_external_tag_category_lookup_expires_at ON blombooru_external_tag_category_lookup_cache(expires_at)",
        ]
        for statement in index_statements:
            conn.execute(text(statement))
        conn.commit()


def migrate_add_pixiv_tag_taxonomy_alias_kb(engine, inspector):
    """Create Phase 4.4-P2R-F4 Pixiv taxonomy/alias KB tables.

    This migration is additive only. It creates dedicated KB/cache tables for
    Pixiv raw-tag taxonomy and alias evidence, and does not alter entity,
    evidence, candidate, assignment, ProviderCache, NegativeLookupCache,
    media_tags, TagTranslation, or LocalSourceHint-style truth/product tables.
    """
    from sqlalchemy import text

    tables = set(inspector.get_table_names())
    is_sqlite = engine.dialect.name == 'sqlite'
    pk_type = 'INTEGER PRIMARY KEY AUTOINCREMENT' if is_sqlite else 'SERIAL PRIMARY KEY'
    now_expr = 'CURRENT_TIMESTAMP' if is_sqlite else 'NOW()'
    json_type = 'JSON' if not is_sqlite else 'JSON'

    with engine.connect() as conn:
        created_taxonomy_table = False
        created_alias_table = False
        if 'blombooru_pixiv_tag_taxonomy_kb' not in tables:
            logger.info("Creating blombooru_pixiv_tag_taxonomy_kb table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_pixiv_tag_taxonomy_kb (
                    id {pk_type},
                    raw_tag VARCHAR(500),
                    normalized_tag VARCHAR(500) NOT NULL,
                    canonical_key VARCHAR(500) NOT NULL,
                    source_scope VARCHAR(100) NOT NULL DEFAULT 'pixiv_raw_tag_v1',
                    language_script_hints {json_type},
                    candidate_namespace VARCHAR(50) NOT NULL DEFAULT 'unknown',
                    confidence FLOAT,
                    status VARCHAR(50) NOT NULL DEFAULT 'unresolved',
                    source_summary {json_type},
                    frequency INTEGER NOT NULL DEFAULT 0,
                    high_value_score FLOAT,
                    unresolved_reason VARCHAR(100),
                    next_action VARCHAR(255),
                    manual_override_status VARCHAR(50) NOT NULL DEFAULT 'none',
                    manual_override_value VARCHAR(500),
                    notes TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_pixiv_tag_taxonomy_scope_key UNIQUE (source_scope, canonical_key)
                )
            """))
            created_taxonomy_table = True

        if 'blombooru_pixiv_tag_alias_kb' not in tables:
            logger.info("Creating blombooru_pixiv_tag_alias_kb table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_pixiv_tag_alias_kb (
                    id {pk_type},
                    source_tag VARCHAR(500) NOT NULL,
                    source_canonical_key VARCHAR(500) NOT NULL,
                    target_tag VARCHAR(500) NOT NULL,
                    target_canonical_key VARCHAR(500) NOT NULL,
                    relation_type VARCHAR(100) NOT NULL,
                    evidence_source VARCHAR(100) NOT NULL,
                    evidence_payload {json_type},
                    confidence FLOAT,
                    status VARCHAR(50) NOT NULL DEFAULT 'candidate',
                    frequency INTEGER NOT NULL DEFAULT 0,
                    manual_override_status VARCHAR(50) NOT NULL DEFAULT 'none',
                    manual_override_value VARCHAR(500),
                    notes TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_pixiv_tag_alias_relation_evidence UNIQUE (
                        source_canonical_key,
                        target_canonical_key,
                        relation_type,
                        evidence_source
                    )
                )
            """))
            created_alias_table = True

        table_columns = {
            table_name: {
                column['name']
                for column in inspector.get_columns(table_name)
            }
            for table_name in (
                'blombooru_pixiv_tag_taxonomy_kb',
                'blombooru_pixiv_tag_alias_kb',
            )
            if table_name in set(inspector.get_table_names())
        }
        taxonomy_add_columns = {
            'source_scope': "ALTER TABLE blombooru_pixiv_tag_taxonomy_kb ADD COLUMN source_scope VARCHAR(100) NOT NULL DEFAULT 'pixiv_raw_tag_v1'",
            'language_script_hints': f"ALTER TABLE blombooru_pixiv_tag_taxonomy_kb ADD COLUMN language_script_hints {json_type}",
            'source_summary': f"ALTER TABLE blombooru_pixiv_tag_taxonomy_kb ADD COLUMN source_summary {json_type}",
            'frequency': "ALTER TABLE blombooru_pixiv_tag_taxonomy_kb ADD COLUMN frequency INTEGER NOT NULL DEFAULT 0",
            'high_value_score': "ALTER TABLE blombooru_pixiv_tag_taxonomy_kb ADD COLUMN high_value_score FLOAT",
            'unresolved_reason': "ALTER TABLE blombooru_pixiv_tag_taxonomy_kb ADD COLUMN unresolved_reason VARCHAR(100)",
            'next_action': "ALTER TABLE blombooru_pixiv_tag_taxonomy_kb ADD COLUMN next_action VARCHAR(255)",
            'manual_override_status': "ALTER TABLE blombooru_pixiv_tag_taxonomy_kb ADD COLUMN manual_override_status VARCHAR(50) NOT NULL DEFAULT 'none'",
            'manual_override_value': "ALTER TABLE blombooru_pixiv_tag_taxonomy_kb ADD COLUMN manual_override_value VARCHAR(500)",
        }
        if not created_taxonomy_table:
            for column_name, statement in taxonomy_add_columns.items():
                if column_name not in table_columns.get('blombooru_pixiv_tag_taxonomy_kb', set()):
                    conn.execute(text(statement))

        alias_add_columns = {
            'evidence_payload': f"ALTER TABLE blombooru_pixiv_tag_alias_kb ADD COLUMN evidence_payload {json_type}",
            'frequency': "ALTER TABLE blombooru_pixiv_tag_alias_kb ADD COLUMN frequency INTEGER NOT NULL DEFAULT 0",
            'manual_override_status': "ALTER TABLE blombooru_pixiv_tag_alias_kb ADD COLUMN manual_override_status VARCHAR(50) NOT NULL DEFAULT 'none'",
            'manual_override_value': "ALTER TABLE blombooru_pixiv_tag_alias_kb ADD COLUMN manual_override_value VARCHAR(500)",
        }
        if not created_alias_table:
            for column_name, statement in alias_add_columns.items():
                if column_name not in table_columns.get('blombooru_pixiv_tag_alias_kb', set()):
                    conn.execute(text(statement))

        index_statements = [
            "CREATE INDEX IF NOT EXISTS ix_pixiv_tag_taxonomy_status_namespace ON blombooru_pixiv_tag_taxonomy_kb(status, candidate_namespace)",
            "CREATE INDEX IF NOT EXISTS ix_pixiv_tag_taxonomy_canonical_key ON blombooru_pixiv_tag_taxonomy_kb(canonical_key)",
            "CREATE INDEX IF NOT EXISTS ix_pixiv_tag_taxonomy_unresolved_reason ON blombooru_pixiv_tag_taxonomy_kb(unresolved_reason)",
            "CREATE INDEX IF NOT EXISTS ix_pixiv_tag_taxonomy_updated ON blombooru_pixiv_tag_taxonomy_kb(updated_at)",
            "CREATE INDEX IF NOT EXISTS ix_pixiv_tag_alias_relation_status ON blombooru_pixiv_tag_alias_kb(relation_type, status)",
            "CREATE INDEX IF NOT EXISTS ix_pixiv_tag_alias_source_key ON blombooru_pixiv_tag_alias_kb(source_canonical_key)",
            "CREATE INDEX IF NOT EXISTS ix_pixiv_tag_alias_target_key ON blombooru_pixiv_tag_alias_kb(target_canonical_key)",
        ]
        for statement in index_statements:
            conn.execute(text(statement))
        conn.commit()


def migrate_add_source_metadata_name_registry(engine, inspector):
    """Create Phase 4.4-P2R-F5 provider-neutral source metadata/name tables.

    This migration is additive only. It creates source-layer metadata, tag,
    name, alias-candidate, registry, and evidence-staging tables. It does not
    alter Entity, EntityAlias, EntityEvidence, MediaEntityCandidate,
    MediaEntityAssignment, ProviderCache, NegativeLookupCache, media_tags,
    TagTranslation, LocalSourceHint-style tables, or confirmed assignments.
    """
    from sqlalchemy import text

    tables = set(inspector.get_table_names())
    is_sqlite = engine.dialect.name == 'sqlite'
    pk_type = 'INTEGER PRIMARY KEY AUTOINCREMENT' if is_sqlite else 'SERIAL PRIMARY KEY'
    now_expr = 'CURRENT_TIMESTAMP' if is_sqlite else 'NOW()'
    json_type = 'JSON' if not is_sqlite else 'JSON'
    bool_true = '1' if is_sqlite else 'TRUE'

    with engine.connect() as conn:
        if 'blombooru_source_metadata_records' not in tables:
            logger.info("Creating blombooru_source_metadata_records table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_source_metadata_records (
                    id {pk_type},
                    provider VARCHAR(100) NOT NULL,
                    provider_run_id VARCHAR(255),
                    run_label VARCHAR(255),
                    provider_record_key VARCHAR(500) NOT NULL,
                    media_id INTEGER,
                    source_work_id VARCHAR(255),
                    source_page_index INTEGER,
                    source_url VARCHAR(1000),
                    title VARCHAR(1000),
                    artist_name VARCHAR(500),
                    artist_id VARCHAR(255),
                    confidence FLOAT,
                    similarity FLOAT,
                    metadata_kind VARCHAR(100) NOT NULL DEFAULT 'provider_metadata',
                    data_type_label VARCHAR(100) NOT NULL DEFAULT 'fixture_or_mock',
                    raw_metadata_json {json_type},
                    provenance {json_type},
                    status VARCHAR(50) NOT NULL DEFAULT 'observed',
                    retrieved_at TIMESTAMP WITH TIME ZONE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_source_metadata_provider_record_key UNIQUE (provider, provider_record_key)
                )
            """))
        else:
            columns = {column['name'] for column in inspector.get_columns('blombooru_source_metadata_records')}
            if 'data_type_label' not in columns:
                logger.info("Adding blombooru_source_metadata_records.data_type_label...")
                conn.execute(text(
                    "ALTER TABLE blombooru_source_metadata_records "
                    "ADD COLUMN data_type_label VARCHAR(100) NOT NULL DEFAULT 'fixture_or_mock'"
                ))

        if 'blombooru_source_tag_observations' not in tables:
            logger.info("Creating blombooru_source_tag_observations table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_source_tag_observations (
                    id {pk_type},
                    source_metadata_record_id INTEGER NOT NULL REFERENCES blombooru_source_metadata_records(id) ON DELETE CASCADE,
                    provider VARCHAR(100) NOT NULL,
                    observation_key VARCHAR(500) NOT NULL,
                    raw_tag VARCHAR(500) NOT NULL,
                    normalized_tag VARCHAR(500) NOT NULL,
                    canonical_tag_key VARCHAR(500) NOT NULL,
                    source_tag_kind VARCHAR(100) NOT NULL DEFAULT 'provider_tag',
                    source_category_raw VARCHAR(100),
                    language_hint VARCHAR(50),
                    confidence FLOAT,
                    order_index INTEGER,
                    taxonomy_kb_id INTEGER,
                    status VARCHAR(50) NOT NULL DEFAULT 'observed',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_source_tag_observation_record_key UNIQUE (
                        source_metadata_record_id,
                        observation_key
                    )
                )
            """))

        if 'blombooru_source_tag_registry' not in tables:
            logger.info("Creating blombooru_source_tag_registry table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_source_tag_registry (
                    id {pk_type},
                    provider_scope VARCHAR(100) NOT NULL DEFAULT 'global',
                    normalized_tag VARCHAR(500) NOT NULL,
                    canonical_tag_key VARCHAR(500) NOT NULL,
                    raw_variants_json {json_type},
                    first_seen_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    last_seen_at TIMESTAMP WITH TIME ZONE,
                    seen_count INTEGER NOT NULL DEFAULT 0,
                    example_source_metadata_id INTEGER,
                    taxonomy_status VARCHAR(50) NOT NULL DEFAULT 'unclassified',
                    governance_status VARCHAR(50) NOT NULL DEFAULT 'candidate',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_source_tag_registry_scope_key UNIQUE (provider_scope, canonical_tag_key)
                )
            """))

        if 'blombooru_source_name_observations' not in tables:
            logger.info("Creating blombooru_source_name_observations table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_source_name_observations (
                    id {pk_type},
                    source_metadata_record_id INTEGER NOT NULL REFERENCES blombooru_source_metadata_records(id) ON DELETE CASCADE,
                    provider VARCHAR(100) NOT NULL,
                    observation_key VARCHAR(500) NOT NULL,
                    media_id INTEGER,
                    source_work_id VARCHAR(255),
                    source_page_index INTEGER,
                    raw_name VARCHAR(500) NOT NULL,
                    normalized_name VARCHAR(500) NOT NULL,
                    canonical_name_key VARCHAR(500) NOT NULL,
                    name_role VARCHAR(100) NOT NULL,
                    source_field VARCHAR(100) NOT NULL,
                    language_hint VARCHAR(50),
                    script_hint VARCHAR(50),
                    confidence FLOAT,
                    provenance {json_type},
                    requires_review BOOLEAN NOT NULL DEFAULT {bool_true},
                    status VARCHAR(50) NOT NULL DEFAULT 'observed',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_source_name_observation_record_key UNIQUE (
                        source_metadata_record_id,
                        observation_key
                    )
                )
            """))

        if 'blombooru_source_name_registry' not in tables:
            logger.info("Creating blombooru_source_name_registry table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_source_name_registry (
                    id {pk_type},
                    canonical_name_key VARCHAR(500) NOT NULL,
                    primary_display_name VARCHAR(500) NOT NULL,
                    normalized_display_name VARCHAR(500) NOT NULL,
                    raw_variants_json {json_type},
                    provider_coverage_json {json_type},
                    role_distribution_json {json_type},
                    first_seen_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    last_seen_at TIMESTAMP WITH TIME ZONE,
                    seen_count INTEGER NOT NULL DEFAULT 0,
                    governance_status VARCHAR(50) NOT NULL DEFAULT 'candidate',
                    manual_override_status VARCHAR(50) NOT NULL DEFAULT 'none',
                    notes TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_source_name_registry_key UNIQUE (canonical_name_key)
                )
            """))

        if 'blombooru_source_name_alias_candidates' not in tables:
            logger.info("Creating blombooru_source_name_alias_candidates table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_source_name_alias_candidates (
                    id {pk_type},
                    source_name_key VARCHAR(500) NOT NULL,
                    target_name_key VARCHAR(500) NOT NULL,
                    source_display_name VARCHAR(500) NOT NULL,
                    target_display_name VARCHAR(500) NOT NULL,
                    relation_type VARCHAR(100) NOT NULL,
                    evidence_source VARCHAR(100) NOT NULL,
                    evidence_payload {json_type},
                    confidence FLOAT,
                    status VARCHAR(50) NOT NULL DEFAULT 'candidate',
                    requires_review BOOLEAN NOT NULL DEFAULT {bool_true},
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_source_name_alias_relation_evidence UNIQUE (
                        source_name_key,
                        target_name_key,
                        relation_type,
                        evidence_source
                    )
                )
            """))

        if 'blombooru_source_metadata_evidence' not in tables:
            logger.info("Creating blombooru_source_metadata_evidence table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_source_metadata_evidence (
                    id {pk_type},
                    source_metadata_record_id INTEGER NOT NULL REFERENCES blombooru_source_metadata_records(id) ON DELETE CASCADE,
                    evidence_key VARCHAR(500) NOT NULL,
                    observation_type VARCHAR(100) NOT NULL,
                    observation_id INTEGER,
                    evidence_kind VARCHAR(100) NOT NULL,
                    evidence_strength VARCHAR(50) NOT NULL DEFAULT 'unknown',
                    provenance {json_type},
                    status VARCHAR(50) NOT NULL DEFAULT 'staged',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_source_metadata_evidence_record_key UNIQUE (
                        source_metadata_record_id,
                        evidence_key
                    )
                )
            """))

        if 'blombooru_source_searchable_name_assertions' not in tables:
            logger.info("Creating blombooru_source_searchable_name_assertions table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_source_searchable_name_assertions (
                    id {pk_type},
                    provider VARCHAR(100) NOT NULL,
                    source_metadata_record_id INTEGER REFERENCES blombooru_source_metadata_records(id) ON DELETE CASCADE,
                    source_tag_observation_id INTEGER REFERENCES blombooru_source_tag_observations(id) ON DELETE SET NULL,
                    source_name_observation_id INTEGER REFERENCES blombooru_source_name_observations(id) ON DELETE SET NULL,
                    assertion_key VARCHAR(700) NOT NULL,
                    raw_input VARCHAR(500) NOT NULL,
                    normalized_input VARCHAR(500) NOT NULL,
                    canonical_name_key VARCHAR(500) NOT NULL,
                    asserted_name VARCHAR(500),
                    asserted_role VARCHAR(100) NOT NULL,
                    status VARCHAR(50) NOT NULL DEFAULT 'needs_review',
                    confidence VARCHAR(50) NOT NULL DEFAULT 'low',
                    confidence_score FLOAT,
                    evidence_sources_json {json_type},
                    model_name VARCHAR(255),
                    prompt_version VARCHAR(100),
                    structured_output_schema_version VARCHAR(100) NOT NULL,
                    reasoning_summary_private TEXT,
                    provenance_summary {json_type},
                    requires_review BOOLEAN NOT NULL DEFAULT {bool_true},
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_source_searchable_name_assertion_key UNIQUE (assertion_key)
                )
            """))

        index_statements = [
            "CREATE INDEX IF NOT EXISTS ix_source_metadata_provider_status ON blombooru_source_metadata_records(provider, status)",
            "CREATE INDEX IF NOT EXISTS ix_source_metadata_media_provider ON blombooru_source_metadata_records(media_id, provider)",
            "CREATE INDEX IF NOT EXISTS ix_source_metadata_work_page ON blombooru_source_metadata_records(source_work_id, source_page_index)",
            "CREATE INDEX IF NOT EXISTS ix_source_metadata_data_type ON blombooru_source_metadata_records(data_type_label)",
            "CREATE INDEX IF NOT EXISTS ix_source_tag_observation_provider_kind ON blombooru_source_tag_observations(provider, source_tag_kind)",
            "CREATE INDEX IF NOT EXISTS ix_source_tag_observation_canonical ON blombooru_source_tag_observations(canonical_tag_key)",
            "CREATE INDEX IF NOT EXISTS ix_source_tag_registry_governance ON blombooru_source_tag_registry(governance_status)",
            "CREATE INDEX IF NOT EXISTS ix_source_tag_registry_taxonomy ON blombooru_source_tag_registry(taxonomy_status)",
            "CREATE INDEX IF NOT EXISTS ix_source_name_observation_provider_role ON blombooru_source_name_observations(provider, name_role)",
            "CREATE INDEX IF NOT EXISTS ix_source_name_observation_canonical ON blombooru_source_name_observations(canonical_name_key)",
            "CREATE INDEX IF NOT EXISTS ix_source_name_observation_media_role ON blombooru_source_name_observations(media_id, name_role)",
            "CREATE INDEX IF NOT EXISTS ix_source_name_registry_governance ON blombooru_source_name_registry(governance_status)",
            "CREATE INDEX IF NOT EXISTS ix_source_name_registry_manual_override ON blombooru_source_name_registry(manual_override_status)",
            "CREATE INDEX IF NOT EXISTS ix_source_name_alias_source_key ON blombooru_source_name_alias_candidates(source_name_key)",
            "CREATE INDEX IF NOT EXISTS ix_source_name_alias_target_key ON blombooru_source_name_alias_candidates(target_name_key)",
            "CREATE INDEX IF NOT EXISTS ix_source_name_alias_relation_status ON blombooru_source_name_alias_candidates(relation_type, status)",
            "CREATE INDEX IF NOT EXISTS ix_source_metadata_evidence_kind_status ON blombooru_source_metadata_evidence(evidence_kind, status)",
            "CREATE INDEX IF NOT EXISTS ix_source_metadata_evidence_observation ON blombooru_source_metadata_evidence(observation_type, observation_id)",
            "CREATE INDEX IF NOT EXISTS ix_source_searchable_name_assertion_provider_status ON blombooru_source_searchable_name_assertions(provider, status)",
            "CREATE INDEX IF NOT EXISTS ix_source_searchable_name_assertion_canonical_status ON blombooru_source_searchable_name_assertions(canonical_name_key, status)",
            "CREATE INDEX IF NOT EXISTS ix_source_searchable_name_assertion_role_status ON blombooru_source_searchable_name_assertions(asserted_role, status)",
            "CREATE INDEX IF NOT EXISTS ix_source_searchable_name_assertion_tag_observation ON blombooru_source_searchable_name_assertions(source_tag_observation_id)",
            "CREATE INDEX IF NOT EXISTS ix_source_searchable_name_assertion_name_observation ON blombooru_source_searchable_name_assertions(source_name_observation_id)",
        ]
        for statement in index_statements:
            conn.execute(text(statement))
        conn.commit()


def migrate_add_source_name_candidate_extraction(engine, inspector):
    """Create Phase 4.4-P2R-F7a source name candidate extraction tables.

    This migration is additive only. It stores unconfirmed source-layer name
    candidate extraction runs, record-level verdicts, and candidate rows. It
    does not alter Entity, EntityAlias, EntityEvidence, MediaEntityCandidate,
    MediaEntityAssignment, LocalSourceHint-style tables, media_tags,
    TagTranslation, ProviderCache, NegativeLookupCache, or confirmed
    assignments.
    """
    from sqlalchemy import text

    tables = set(inspector.get_table_names())
    is_sqlite = engine.dialect.name == 'sqlite'
    pk_type = 'INTEGER PRIMARY KEY AUTOINCREMENT' if is_sqlite else 'SERIAL PRIMARY KEY'
    now_expr = 'CURRENT_TIMESTAMP' if is_sqlite else 'NOW()'
    json_type = 'JSON' if not is_sqlite else 'JSON'

    with engine.connect() as conn:
        if 'blombooru_source_name_candidate_extraction_runs' not in tables:
            logger.info("Creating blombooru_source_name_candidate_extraction_runs table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_source_name_candidate_extraction_runs (
                    id {pk_type},
                    run_id VARCHAR(255) NOT NULL,
                    run_label VARCHAR(255),
                    extractor_version VARCHAR(100) NOT NULL,
                    prompt_version VARCHAR(100),
                    structured_output_schema_version VARCHAR(100) NOT NULL,
                    mode VARCHAR(100) NOT NULL DEFAULT 'dry_run',
                    status VARCHAR(50) NOT NULL DEFAULT 'running',
                    input_scope_json {json_type},
                    summary_json {json_type},
                    provider_summary_json {json_type},
                    started_at TIMESTAMP WITH TIME ZONE,
                    finished_at TIMESTAMP WITH TIME ZONE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_source_name_candidate_run_id UNIQUE (run_id)
                )
            """))

        if 'blombooru_source_name_candidate_record_verdicts' not in tables:
            logger.info("Creating blombooru_source_name_candidate_record_verdicts table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_source_name_candidate_record_verdicts (
                    id {pk_type},
                    extraction_run_id INTEGER NOT NULL REFERENCES blombooru_source_name_candidate_extraction_runs(id) ON DELETE CASCADE,
                    source_metadata_record_id INTEGER REFERENCES blombooru_source_metadata_records(id) ON DELETE SET NULL,
                    media_id INTEGER REFERENCES blombooru_media(id) ON DELETE SET NULL,
                    provider VARCHAR(100) NOT NULL,
                    group_key VARCHAR(700) NOT NULL,
                    extraction_verdict VARCHAR(100) NOT NULL,
                    verdict_reason TEXT,
                    no_name_reason VARCHAR(255),
                    candidate_count INTEGER NOT NULL DEFAULT 0,
                    rejected_count INTEGER NOT NULL DEFAULT 0,
                    meta_count INTEGER NOT NULL DEFAULT 0,
                    ambiguous_count INTEGER NOT NULL DEFAULT 0,
                    confidence_summary {json_type},
                    extraction_warnings_json {json_type},
                    evidence_payload {json_type},
                    status VARCHAR(50) NOT NULL DEFAULT 'observed',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_source_name_candidate_record_verdict_run_group UNIQUE (
                        extraction_run_id,
                        group_key
                    )
                )
            """))

        if 'blombooru_source_name_candidates' not in tables:
            logger.info("Creating blombooru_source_name_candidates table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_source_name_candidates (
                    id {pk_type},
                    extraction_run_id INTEGER REFERENCES blombooru_source_name_candidate_extraction_runs(id) ON DELETE SET NULL,
                    record_verdict_id INTEGER REFERENCES blombooru_source_name_candidate_record_verdicts(id) ON DELETE SET NULL,
                    source_metadata_record_id INTEGER REFERENCES blombooru_source_metadata_records(id) ON DELETE SET NULL,
                    media_id INTEGER REFERENCES blombooru_media(id) ON DELETE SET NULL,
                    provider VARCHAR(100) NOT NULL,
                    group_key VARCHAR(700) NOT NULL,
                    candidate_key VARCHAR(900) NOT NULL,
                    origin_type VARCHAR(100) NOT NULL,
                    origin_id VARCHAR(500),
                    raw_value VARCHAR(500) NOT NULL,
                    display_name VARCHAR(500) NOT NULL,
                    normalized_value VARCHAR(500) NOT NULL,
                    canonical_key VARCHAR(500) NOT NULL,
                    candidate_role VARCHAR(100) NOT NULL,
                    candidate_status VARCHAR(50) NOT NULL DEFAULT 'active_candidate',
                    extraction_verdict VARCHAR(100) NOT NULL,
                    language_hint VARCHAR(50),
                    script_hint VARCHAR(50),
                    work_context VARCHAR(500),
                    work_context_key VARCHAR(500),
                    parenthetical_base VARCHAR(500),
                    parenthetical_context VARCHAR(500),
                    extraction_action VARCHAR(100) NOT NULL,
                    confidence FLOAT,
                    reason TEXT,
                    rejection_reason VARCHAR(255),
                    no_name_reason VARCHAR(255),
                    evidence_payload {json_type},
                    extractor_version VARCHAR(100) NOT NULL,
                    status VARCHAR(50) NOT NULL DEFAULT 'active',
                    superseded_by_candidate_id INTEGER,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_source_name_candidate_key UNIQUE (candidate_key)
                )
            """))

        index_statements = [
            "CREATE INDEX IF NOT EXISTS ix_source_name_candidate_extraction_run_status ON blombooru_source_name_candidate_extraction_runs(status)",
            "CREATE INDEX IF NOT EXISTS ix_source_name_candidate_extraction_run_mode ON blombooru_source_name_candidate_extraction_runs(mode)",
            "CREATE INDEX IF NOT EXISTS ix_source_name_candidate_record_verdict_provider ON blombooru_source_name_candidate_record_verdicts(provider)",
            "CREATE INDEX IF NOT EXISTS ix_source_name_candidate_record_verdict_verdict ON blombooru_source_name_candidate_record_verdicts(extraction_verdict)",
            "CREATE INDEX IF NOT EXISTS ix_source_name_candidate_record_verdict_source_record ON blombooru_source_name_candidate_record_verdicts(source_metadata_record_id)",
            "CREATE INDEX IF NOT EXISTS ix_source_name_candidate_record_verdict_media ON blombooru_source_name_candidate_record_verdicts(media_id)",
            "CREATE INDEX IF NOT EXISTS ix_source_name_candidate_run_candidate_status ON blombooru_source_name_candidates(extraction_run_id, candidate_status)",
            "CREATE INDEX IF NOT EXISTS ix_source_name_candidate_provider_role ON blombooru_source_name_candidates(provider, candidate_role)",
            "CREATE INDEX IF NOT EXISTS ix_source_name_candidate_canonical_status ON blombooru_source_name_candidates(canonical_key, candidate_status)",
            "CREATE INDEX IF NOT EXISTS ix_source_name_candidate_origin ON blombooru_source_name_candidates(origin_type, origin_id)",
            "CREATE INDEX IF NOT EXISTS ix_source_name_candidate_source_record ON blombooru_source_name_candidates(source_metadata_record_id)",
            "CREATE INDEX IF NOT EXISTS ix_source_name_candidate_media ON blombooru_source_name_candidates(media_id)",
            "CREATE INDEX IF NOT EXISTS ix_source_name_candidate_language ON blombooru_source_name_candidates(language_hint)",
            "CREATE INDEX IF NOT EXISTS ix_source_name_candidate_script ON blombooru_source_name_candidates(script_hint)",
            "CREATE INDEX IF NOT EXISTS ix_source_name_candidate_work_context ON blombooru_source_name_candidates(work_context_key)",
            "CREATE INDEX IF NOT EXISTS ix_source_name_candidate_extraction_action ON blombooru_source_name_candidates(extraction_action)",
            "CREATE INDEX IF NOT EXISTS ix_source_name_candidate_rejection_reason ON blombooru_source_name_candidates(rejection_reason)",
        ]
        for statement in index_statements:
            conn.execute(text(statement))
        conn.commit()


def migrate_add_dynamic_library_sync_tables(engine, inspector):
    """Create durable dynamic library sync state tables (Phase 4.7-S1).

    Additive only: this records source roots, per-source item state, check runs,
    and per-run item observations. It does not import media or mutate source
    files.
    """
    from sqlalchemy import text

    tables = set(inspector.get_table_names())
    is_sqlite = engine.dialect.name == 'sqlite'
    pk_type = 'INTEGER PRIMARY KEY AUTOINCREMENT' if is_sqlite else 'SERIAL PRIMARY KEY'
    big_int = 'INTEGER' if is_sqlite else 'BIGINT'
    now_expr = 'CURRENT_TIMESTAMP' if is_sqlite else 'NOW()'
    json_type = 'JSON' if not is_sqlite else 'JSON'
    bool_false = '0' if is_sqlite else 'false'
    bool_true = '1' if is_sqlite else 'true'

    with engine.connect() as conn:
        if 'blombooru_dynamic_source_roots' not in tables:
            logger.info("Creating blombooru_dynamic_source_roots table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_dynamic_source_roots (
                    id {pk_type},
                    label VARCHAR(255) NOT NULL,
                    root_path VARCHAR(2000) NOT NULL,
                    root_path_hash VARCHAR(128) NOT NULL,
                    source_type VARCHAR(50) NOT NULL DEFAULT 'local_path',
                    is_active BOOLEAN NOT NULL DEFAULT {bool_true},
                    auto_sync_enabled BOOLEAN NOT NULL DEFAULT {bool_false},
                    sync_threshold INTEGER NOT NULL DEFAULT 100,
                    notes TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    last_checked_at TIMESTAMP WITH TIME ZONE,
                    CONSTRAINT uq_dynamic_source_root_path_hash UNIQUE (root_path_hash)
                )
            """))

        if 'blombooru_dynamic_sync_runs' not in tables:
            logger.info("Creating blombooru_dynamic_sync_runs table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_dynamic_sync_runs (
                    id {pk_type},
                    run_type VARCHAR(50) NOT NULL DEFAULT 'check',
                    mode VARCHAR(50) NOT NULL DEFAULT 'dry_run',
                    status VARCHAR(50) NOT NULL DEFAULT 'running',
                    dry_run BOOLEAN NOT NULL DEFAULT {bool_true},
                    threshold INTEGER NOT NULL DEFAULT 100,
                    threshold_reached BOOLEAN NOT NULL DEFAULT {bool_false},
                    roots_checked INTEGER NOT NULL DEFAULT 0,
                    total_seen INTEGER NOT NULL DEFAULT 0,
                    new_items INTEGER NOT NULL DEFAULT 0,
                    changed_items INTEGER NOT NULL DEFAULT 0,
                    unchanged_items INTEGER NOT NULL DEFAULT 0,
                    deferred_items INTEGER NOT NULL DEFAULT 0,
                    failed_items INTEGER NOT NULL DEFAULT 0,
                    missing_items INTEGER NOT NULL DEFAULT 0,
                    pending_import_items INTEGER NOT NULL DEFAULT 0,
                    summary_json {json_type},
                    error_message TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    started_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    finished_at TIMESTAMP WITH TIME ZONE
                )
            """))

        if 'blombooru_dynamic_source_items' not in tables:
            logger.info("Creating blombooru_dynamic_source_items table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_dynamic_source_items (
                    id {pk_type},
                    source_root_id INTEGER NOT NULL REFERENCES blombooru_dynamic_source_roots(id) ON DELETE CASCADE,
                    relative_path VARCHAR(2000) NOT NULL,
                    relative_path_hash VARCHAR(128) NOT NULL,
                    file_size {big_int},
                    mtime FLOAT,
                    mtime_ns {big_int},
                    content_hash VARCHAR(128),
                    media_id INTEGER REFERENCES blombooru_media(id) ON DELETE SET NULL,
                    source_status VARCHAR(50) NOT NULL DEFAULT 'available',
                    sync_state VARCHAR(50) NOT NULL DEFAULT 'new',
                    import_status VARCHAR(50) NOT NULL DEFAULT 'pending',
                    classification_status VARCHAR(50) NOT NULL DEFAULT 'waiting_import',
                    ai_tagging_status VARCHAR(50) NOT NULL DEFAULT 'waiting_import',
                    localization_status VARCHAR(50) NOT NULL DEFAULT 'waiting_ai_tags',
                    failure_reason VARCHAR(255),
                    deferred_reason VARCHAR(255),
                    first_seen_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    last_seen_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    last_checked_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    last_imported_at TIMESTAMP WITH TIME ZONE,
                    last_sync_run_id INTEGER REFERENCES blombooru_dynamic_sync_runs(id) ON DELETE SET NULL,
                    last_seen_run_id INTEGER REFERENCES blombooru_dynamic_sync_runs(id) ON DELETE SET NULL,
                    metadata_json {json_type},
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_dynamic_source_item_root_relhash UNIQUE (source_root_id, relative_path_hash)
                )
            """))

        if 'blombooru_dynamic_sync_run_items' not in tables:
            logger.info("Creating blombooru_dynamic_sync_run_items table...")
            conn.execute(text(f"""
                CREATE TABLE blombooru_dynamic_sync_run_items (
                    id {pk_type},
                    sync_run_id INTEGER NOT NULL REFERENCES blombooru_dynamic_sync_runs(id) ON DELETE CASCADE,
                    source_item_id INTEGER NOT NULL REFERENCES blombooru_dynamic_source_items(id) ON DELETE CASCADE,
                    item_state VARCHAR(50) NOT NULL,
                    action VARCHAR(50) NOT NULL DEFAULT 'record_only',
                    reason VARCHAR(255),
                    eligible_for_db_import BOOLEAN NOT NULL DEFAULT {bool_false},
                    bytes_copied {big_int} NOT NULL DEFAULT 0,
                    media_id INTEGER REFERENCES blombooru_media(id) ON DELETE SET NULL,
                    previous_metadata_json {json_type},
                    current_metadata_json {json_type},
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT {now_expr},
                    CONSTRAINT uq_dynamic_sync_run_item UNIQUE (sync_run_id, source_item_id)
                )
            """))

        index_statements = [
            "CREATE INDEX IF NOT EXISTS ix_dynamic_source_roots_active ON blombooru_dynamic_source_roots(is_active)",
            "CREATE INDEX IF NOT EXISTS ix_dynamic_source_items_root_relhash ON blombooru_dynamic_source_items(source_root_id, relative_path_hash)",
            "CREATE INDEX IF NOT EXISTS ix_dynamic_source_items_content_hash ON blombooru_dynamic_source_items(content_hash)",
            "CREATE INDEX IF NOT EXISTS ix_dynamic_source_items_import_status ON blombooru_dynamic_source_items(import_status)",
            "CREATE INDEX IF NOT EXISTS ix_dynamic_source_items_classification_status ON blombooru_dynamic_source_items(classification_status)",
            "CREATE INDEX IF NOT EXISTS ix_dynamic_source_items_ai_tagging_status ON blombooru_dynamic_source_items(ai_tagging_status)",
            "CREATE INDEX IF NOT EXISTS ix_dynamic_source_items_localization_status ON blombooru_dynamic_source_items(localization_status)",
            "CREATE INDEX IF NOT EXISTS ix_dynamic_source_items_last_seen_run ON blombooru_dynamic_source_items(last_seen_run_id)",
            "CREATE INDEX IF NOT EXISTS ix_dynamic_source_items_media_id ON blombooru_dynamic_source_items(media_id)",
            "CREATE INDEX IF NOT EXISTS ix_dynamic_sync_runs_status_created ON blombooru_dynamic_sync_runs(status, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_dynamic_sync_runs_mode_status ON blombooru_dynamic_sync_runs(mode, status)",
            "CREATE INDEX IF NOT EXISTS ix_dynamic_sync_run_items_run_state ON blombooru_dynamic_sync_run_items(sync_run_id, item_state)",
            "CREATE INDEX IF NOT EXISTS ix_dynamic_sync_run_items_item ON blombooru_dynamic_sync_run_items(source_item_id)",
            "CREATE INDEX IF NOT EXISTS ix_dynamic_sync_run_items_import_eligible ON blombooru_dynamic_sync_run_items(eligible_for_db_import)",
        ]
        for statement in index_statements:
            conn.execute(text(statement))
        conn.commit()
