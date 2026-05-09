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
