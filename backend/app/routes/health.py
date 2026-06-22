from fastapi import APIRouter
from sqlalchemy import inspect, text

from .. import database
from ..config import APP_VERSION, settings

router = APIRouter(prefix="/api", tags=["health"])

REQUIRED_CORE_TABLES = frozenset(
    {
        "blombooru_media",
        "blombooru_tags",
        "blombooru_media_tags",
        "blombooru_users",
    }
)


def _active_engine():
    if settings.IS_FIRST_RUN:
        return None
    active_engine = database.engine or database.init_engine()
    return active_engine


def _db_reachable(active_engine) -> bool:
    if active_engine is None:
        return False
    try:
        with active_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _schema_compatibility(active_engine) -> tuple[bool, str]:
    if active_engine is None:
        return False, "db_unavailable"
    try:
        inspector = inspect(active_engine)
        tables = set(inspector.get_table_names())
    except Exception:
        return False, "schema_check_failed"
    if not REQUIRED_CORE_TABLES.issubset(tables):
        return False, "missing_required_tables"
    return True, "compatible"


@router.get("/health")
async def get_health():
    active_engine = _active_engine()
    db_reachable = _db_reachable(active_engine)
    schema_compatible, schema_status = (
        _schema_compatibility(active_engine) if db_reachable else (False, "db_unreachable")
    )
    storage_configured = settings.STORAGE_ROOT_EXPLICITLY_SET
    debug = settings.DEBUG
    return {
        "ok": bool(db_reachable and schema_compatible and storage_configured and not debug),
        "app_name": "V.I.O.L.E.T.",
        "version": APP_VERSION,
        "env": settings.VIOLET_ENV,
        "db_reachable": db_reachable,
        "schema_compatible": schema_compatible,
        "schema_status": schema_status,
        "storage_configured": storage_configured,
        "debug": debug,
    }
