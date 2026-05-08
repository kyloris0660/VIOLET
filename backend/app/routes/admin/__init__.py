"""
Admin routes package.

Routes are split into focused sub-modules and combined onto a single APIRouter
so the rest of the app sees no difference (still `admin.router`).

`import_tags_csv_logic` is re-exported here because `utils/backup.py` imports
it directly from this package.
"""
from fastapi import APIRouter

from .api_keys import router as _api_keys_router
from .auth import router as _auth_router
from .backup import router as _backup_router
from .media import router as _media_router
from .onboarding import router as _onboarding_router
from .settings import router as _settings_router
from .shared_tags import router as _shared_tags_router
from .tags import import_tags_csv_logic, router as _tags_router
from .ai_tagging import router as _ai_tagging_router
from .ai_tag_review import router as _ai_tag_review_router
from .ai_tagging_jobs import router as _ai_tagging_jobs_router
from .tag_localization import router as _tag_localization_router
from .content_classification import router as _content_classification_router
from .dev_tools import router as _dev_tools_router

router = APIRouter(prefix="/api/admin", tags=["admin"])

router.include_router(_onboarding_router)
router.include_router(_auth_router)
router.include_router(_settings_router)
router.include_router(_media_router)
router.include_router(_tags_router)
router.include_router(_backup_router)
router.include_router(_api_keys_router)
router.include_router(_shared_tags_router)
router.include_router(_ai_tagging_router)
router.include_router(_ai_tag_review_router)
router.include_router(_ai_tagging_jobs_router)
router.include_router(_tag_localization_router)
router.include_router(_content_classification_router)
router.include_router(_dev_tools_router)
