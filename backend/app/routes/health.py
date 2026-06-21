from fastapi import APIRouter
from sqlalchemy import text

from .. import database
from ..config import APP_VERSION, settings

router = APIRouter(prefix="/api", tags=["health"])


def _db_reachable() -> bool:
    if settings.IS_FIRST_RUN:
        return False
    active_engine = database.engine or database.init_engine()
    if active_engine is None:
        return False
    try:
        with active_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@router.get("/health")
async def get_health():
    db_reachable = _db_reachable()
    storage_configured = settings.STORAGE_ROOT_EXPLICITLY_SET
    debug = settings.DEBUG
    return {
        "ok": bool(db_reachable and storage_configured and not debug),
        "app_name": "V.I.O.L.E.T.",
        "version": APP_VERSION,
        "env": settings.VIOLET_ENV,
        "db_reachable": db_reachable,
        "storage_configured": storage_configured,
        "debug": debug,
    }
