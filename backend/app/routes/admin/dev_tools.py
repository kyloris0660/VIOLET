"""Developer / E2E tools API endpoints (Phase 2.3a).

Config diagnostics, E2E test data reset, and recommended config.
Admin-only, requires admin_mode.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...auth import require_admin_mode
from ...config import settings, _PROJECT_ROOT
from ...database import get_db
from ...models import User
from ...utils.logger import logger

router = APIRouter()


BLOCKED_ROOTS = {"", "c:", "c:/", "/", "c:/users"}


def _is_dangerous_path(source_path: str) -> bool:
    """Block dangerous or restricted paths from E2E reset."""
    stripped = source_path.strip()
    if not stripped:
        return True
    lowered = stripped.lower().replace("\\", "/").rstrip("/")
    if not lowered:
        return True
    if lowered in BLOCKED_ROOTS:
        logger.debug("Blocked by BLOCKED_ROOTS: %s -> %s", source_path, lowered)
        return True
    if "icloud" in lowered and "photos" in lowered:
        return True
    project_root = str(_PROJECT_ROOT).lower().replace("\\", "/").rstrip("/")
    if lowered == project_root:
        logger.debug("Blocked by project_root match: %s == %s", lowered, project_root)
        return True
    if lowered.startswith(project_root + "/"):
        logger.debug("Blocked by project_root prefix: %s starts with %s/", lowered, project_root)
        return True
    if lowered == "data" or lowered.endswith("/data"):
        return True
    if lowered == "media/original" or lowered.endswith("/media/original"):
        return True
    return False


class ResetE2ERequest(BaseModel):
    source_path: str
    confirm: bool = False
    dry_run: bool = True


@router.get("/dev/config-diagnostics")
async def get_config_diagnostics(
    current_user: User = Depends(require_admin_mode),
):
    """Return runtime configuration diagnostics (no secrets)."""
    return {
        "ai_tagging": {
            "enabled": settings.AI_TAGGING_ENABLED,
            "batch_max_items": settings.AI_TAGGING_BATCH_MAX_ITEMS,
            "model_name": settings.AI_MODEL_NAME,
            "general_threshold": settings.AI_GENERAL_THRESHOLD,
            "character_threshold": settings.AI_CHARACTER_THRESHOLD,
            "rating_threshold": settings.AI_RATING_THRESHOLD,
            "suggestion_threshold": settings.AI_SUGGESTION_THRESHOLD,
        },
        "auto_tag_after_import": {
            "enabled": settings.AI_AUTO_TAG_AFTER_IMPORT,
            "max_items": settings.AI_AUTO_TAG_AFTER_IMPORT_MAX_ITEMS,
            "only_new": settings.AI_AUTO_TAG_AFTER_IMPORT_ONLY_NEW,
            "dry_run": settings.AI_AUTO_TAG_AFTER_IMPORT_DRY_RUN,
            "force_suggestions": settings.AI_AUTO_TAG_AFTER_IMPORT_FORCE_SUGGESTIONS,
        },
        "tag_localization": {
            "llm_enabled": settings.TAG_TRANSLATION_LLM_ENABLED,
            "auto_enabled": settings.TAG_TRANSLATION_AUTO_ENABLED,
            "auto_max_items": settings.TAG_TRANSLATION_AUTO_MAX_ITEMS,
            "batch_max_items": settings.TAG_TRANSLATION_BATCH_MAX_ITEMS,
            "provider": settings.TAG_TRANSLATION_LLM_PROVIDER,
            "model": settings.TAG_TRANSLATION_LLM_MODEL or "(not configured)",
            "api_key_configured": bool(settings.TAG_TRANSLATION_LLM_API_KEY),
            "base_url_configured": bool(settings.TAG_TRANSLATION_LLM_BASE_URL),
            "background_enabled": settings.TAG_TRANSLATION_BG_ENABLED,
            "background_interval": settings.TAG_TRANSLATION_BG_INTERVAL,
            "background_batch_size": settings.TAG_TRANSLATION_BG_BATCH_SIZE,
            "background_max_per_run": settings.TAG_TRANSLATION_BG_MAX_PER_RUN,
            "background_daily_limit": settings.TAG_TRANSLATION_BG_DAILY_LIMIT,
            "background_error_limit": settings.TAG_TRANSLATION_BG_ERROR_LIMIT,
            "background_priority": settings.TAG_TRANSLATION_BG_PRIORITY,
        },
        "paths": {
            "local_library_paths": [str(p) for p in settings.LOCAL_LIBRARY_PATHS],
        },
        "env_file": str(_PROJECT_ROOT / ".env"),
    }


@router.post("/dev/reset-e2e-test-data")
async def reset_e2e_test_data(
    body: ResetE2ERequest,
    current_user: User = Depends(require_admin_mode),
    db: Session = Depends(get_db),
):
    if _is_dangerous_path(body.source_path):
        raise HTTPException(
            status_code=400,
            detail=f"Blocked: '{body.source_path}' is a dangerous or restricted path",
        )

    from ...services.e2e_reset_service import compute_reset_summary, execute_reset

    summary = compute_reset_summary(db, body.source_path)

    if body.dry_run:
        return {
            "dry_run": True,
            "summary": summary,
            "message": "No data was deleted. Set dry_run=false and confirm=true to execute.",
        }

    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="Must set confirm=true to execute real deletion",
        )

    result = execute_reset(db, body.source_path)
    return {
        "dry_run": False,
        "summary": result,
        "message": "Reset completed successfully",
    }


@router.get("/dev/recommended-e2e-config")
async def get_recommended_e2e_config(
    current_user: User = Depends(require_admin_mode),
):
    return {
        "snippet": """# Recommended E2E Test Configuration
AI_TAGGING_ENABLED=true
AI_TAGGING_BATCH_MAX_ITEMS=200
AI_AUTO_TAG_AFTER_IMPORT=true
AI_AUTO_TAG_AFTER_IMPORT_MAX_ITEMS=200
AI_AUTO_TAG_AFTER_IMPORT_ONLY_NEW=true
AI_AUTO_TAG_AFTER_IMPORT_DRY_RUN=false
AI_AUTO_TAG_AFTER_IMPORT_FORCE_SUGGESTIONS=false
TAG_TRANSLATION_LLM_ENABLED=true
TAG_TRANSLATION_AUTO_ENABLED=true
TAG_TRANSLATION_AUTO_MAX_ITEMS=200
TAG_TRANSLATION_BATCH_MAX_ITEMS=200
LOCAL_LIBRARY_PATHS=C:\\Users\\kyloris\\Pictures\\VioletTest100

# Fill in your LLM API credentials:
# TAG_TRANSLATION_LLM_API_KEY=your-api-key
# TAG_TRANSLATION_LLM_BASE_URL=https://your-api-url
# TAG_TRANSLATION_LLM_MODEL=your-model-name""",
        "note": "Copy these values to your .env file and restart the server.",
    }
