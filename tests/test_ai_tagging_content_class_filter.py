"""Regression tests for AI tagging content_class_filter (Phase 3.2g.1).

Tests in two layers:
  1. Pydantic model validation (CreateAITagJobRequest field semantics)
  2. Route-level behavior (HTTP 400 rejections, scoped resolution, DB-side limit)

The route-level tests mock the DB session and service imports so no real
database, AI tagging, or LLM calls are needed.

Import strategy:
  The target module (app.routes.admin.ai_tagging_jobs) lives inside the
  admin package whose __init__.py eagerly imports all admin routers —
  some of which transitively require optional/system dependencies such as
  libmagic.  To avoid triggering those side-effect imports we pre-register
  a lightweight stub for the ``app.routes.admin`` package in sys.modules
  before importing the target module.  This keeps the tests portable to
  environments without libmagic.

No real AI tagging, no real LLM, no DB mutation.
"""
import asyncio
import importlib
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from pydantic import ValidationError

# Ensure backend is importable
_BACKEND = str(Path(__file__).resolve().parent.parent / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def _ensure_admin_stub():
    """Pre-register ``app.routes.admin`` as a namespace stub so that
    importing ``app.routes.admin.ai_tagging_jobs`` does NOT execute
    the real ``admin/__init__.py`` (which eagerly imports all routers
    and may pull in optional deps like libmagic).

    Only the ``app.routes.admin`` entry is stubbed; ``app`` and
    ``app.routes`` are left to normal import machinery.  If the real
    admin package is already loaded, this is a no-op.
    """
    key = "app.routes.admin"
    if key in sys.modules:
        return  # already loaded — nothing to stub

    # Ensure parent packages are importable first (normal import, not stubs)
    importlib.import_module("app")
    importlib.import_module("app.routes")

    # Minimal stub — just enough to let importlib resolve child modules
    stub = types.ModuleType(key)
    stub.__path__ = [
        str(Path(__file__).resolve().parent.parent / "backend" / "app" / "routes" / "admin")
    ]
    stub.__package__ = key
    sys.modules[key] = stub


# Apply once at module load
_admin_was_loaded = "app.routes.admin" in sys.modules
_ensure_admin_stub()

# Now import the target module — relative imports inside it will resolve
# normally because ``app``, ``app.routes`` are real, and
# ``app.routes.admin`` is our stub with __path__ pointing to the real dir.
from app.routes.admin.ai_tagging_jobs import (  # noqa: E402
    CreateAITagJobRequest,
    create_ai_tag_job as _create_ai_tag_job_route,
)

# Clean up the stub so that later test files in the same pytest process
# can import the real admin package (which exposes .router etc.).
if not _admin_was_loaded:
    del sys.modules["app.routes.admin"]


class TestCreateAITagJobRequestModel:
    """Test the Pydantic model for content_class_filter field."""

    def test_default_no_content_class_filter(self):
        req = CreateAITagJobRequest()
        assert req.content_class_filter is None

    def test_valid_content_class_filter(self):
        req = CreateAITagJobRequest(
            content_class_filter=["anime", "illustration"],
            max_items=50,
        )
        assert req.content_class_filter == ["anime", "illustration"]
        assert req.max_items == 50

    def test_single_content_class(self):
        req = CreateAITagJobRequest(content_class_filter=["anime"])
        assert req.content_class_filter == ["anime"]

    def test_all_valid_content_classes(self):
        req = CreateAITagJobRequest(
            content_class_filter=["anime", "illustration", "non_anime", "unknown"]
        )
        assert len(req.content_class_filter) == 4

    def test_empty_list_accepted_at_model_level(self):
        """Pydantic model accepts empty list — route handler rejects it with 400.

        The model-level acceptance is by design: Pydantic validates types,
        the route handler enforces business rules (empty list = invalid).
        See TestRouteContentClassFilter.test_empty_list_rejected_with_400.
        """
        req = CreateAITagJobRequest(content_class_filter=[])
        assert req.content_class_filter == []

    def test_media_ids_alone_still_works(self):
        """Existing media_ids-only requests are unaffected."""
        req = CreateAITagJobRequest(media_ids=[1, 2, 3], max_items=10)
        assert req.media_ids == [1, 2, 3]
        assert req.content_class_filter is None


class TestContentClassEnumValues:
    """Verify ContentClassEnum has the expected values."""

    def test_enum_values(self):
        from app.enums import ContentClassEnum
        expected = {"anime", "illustration", "non_anime", "unknown"}
        actual = {e.value for e in ContentClassEnum}
        assert actual == expected


# ---------------------------------------------------------------------------
# Route-level tests for content_class_filter business logic
# ---------------------------------------------------------------------------

def _make_mock_db(media_rows=None):
    """Build a mock SQLAlchemy Session that returns *media_rows* from the
    content_class_filter query chain.

    The mock supports the chained pattern:
        db.query(Media.id).filter(...).filter(...).order_by(...).limit(...).all()
    returning [(id,), ...] for each id in *media_rows*.
    """
    db = MagicMock()
    mock_query = MagicMock()
    # Every .filter(), .order_by(), .limit() returns itself for chaining
    mock_query.filter.return_value = mock_query
    mock_query.order_by.return_value = mock_query
    mock_query.limit.return_value = mock_query
    # .distinct().subquery() for the ai_tagged subquery
    mock_query.distinct.return_value = mock_query
    mock_query.subquery.return_value = MagicMock()

    if media_rows is not None:
        mock_query.all.return_value = [(mid,) for mid in media_rows]
    else:
        mock_query.all.return_value = []

    db.query.return_value = mock_query
    # No active jobs
    mock_query.first.return_value = None
    return db, mock_query


def _make_mock_settings(**overrides):
    """Build a mock settings object with AI tagging enabled."""
    s = MagicMock()
    s.AI_TAGGING_ENABLED = True
    s.AI_TAGGING_BATCH_MAX_ITEMS = overrides.get("batch_max", 500)
    return s


def _make_mock_job(media_ids_json=None):
    """Build a mock AITagJob with serialization-friendly attributes."""
    mock_job = MagicMock()
    mock_job.id = 1
    mock_job.status = "pending"
    mock_job.created_at = None
    mock_job.started_at = None
    mock_job.finished_at = None
    mock_job.media_ids_json = media_ids_json
    mock_job.failed_items_json = None
    return mock_job


class TestRouteContentClassFilter:
    """Route-level tests for create_ai_tag_job endpoint.

    These test the async handler directly with mocked dependencies,
    verifying HTTP 400 rejections and correct scoped resolution.
    Uses asyncio.run() since pytest-asyncio is not installed.
    """

    @pytest.fixture(autouse=True)
    def _patch_deps(self):
        """Patch settings and service imports used by the route handler."""
        self.mock_settings = _make_mock_settings()
        self.patches = [
            patch("app.routes.admin.ai_tagging_jobs.settings", self.mock_settings),
            patch("app.routes.admin.ai_tagging_jobs.require_admin_mode", lambda: MagicMock()),
        ]
        for p in self.patches:
            p.start()
        yield
        for p in self.patches:
            p.stop()

    # -- P1 #1: empty list rejection --

    def test_empty_list_rejected_with_400(self):
        """P1 #1: content_class_filter=[] must be rejected with HTTP 400,
        no job created.  Also asserts service is never called."""
        from fastapi import HTTPException

        db, _ = _make_mock_db()
        body = CreateAITagJobRequest(content_class_filter=[])
        user = MagicMock()

        import app.services.ai_tagging_job_service as svc
        with patch.object(svc, "create_ai_tag_job") as mock_create, \
             patch.object(svc, "is_ai_job_active", return_value=False), \
             patch.object(svc, "start_ai_tag_job"):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(_create_ai_tag_job_route(body=body, current_user=user, db=db))
            mock_create.assert_not_called()
        assert exc_info.value.status_code == 400
        assert "must not be empty" in exc_info.value.detail

    # -- P1 #2: zero-match rejection --

    def test_zero_match_rejected_no_fallback(self):
        """P1 #2: Valid filter that matches zero media → HTTP 400,
        no job created.  Service must not be called."""
        from fastapi import HTTPException

        db, _ = _make_mock_db(media_rows=[])
        body = CreateAITagJobRequest(
            content_class_filter=["anime"],
            max_items=50,
        )
        user = MagicMock()

        import app.services.ai_tagging_job_service as svc
        with patch.object(svc, "create_ai_tag_job") as mock_create, \
             patch.object(svc, "is_ai_job_active", return_value=False), \
             patch.object(svc, "start_ai_tag_job"):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(_create_ai_tag_job_route(body=body, current_user=user, db=db))
            mock_create.assert_not_called()
        assert exc_info.value.status_code == 400
        assert "zero eligible media" in exc_info.value.detail

    # -- None filter → unfiltered (P2 #3: assert service call scope) --

    def test_none_filter_means_unfiltered(self):
        """Omitted / None content_class_filter means no filtering applied.
        The service must be called with media_ids=None (unfiltered scope)."""
        db, mock_query = _make_mock_db()
        body = CreateAITagJobRequest()  # content_class_filter defaults to None
        user = MagicMock()

        mock_job = _make_mock_job(media_ids_json=None)

        import app.services.ai_tagging_job_service as svc
        with patch.object(svc, "create_ai_tag_job", return_value=mock_job) as mock_create, \
             patch.object(svc, "is_ai_job_active", return_value=False), \
             patch.object(svc, "start_ai_tag_job"):
            result = asyncio.run(
                _create_ai_tag_job_route(body=body, current_user=user, db=db)
            )

        # Assert the serialized response shows no media_ids
        assert result["media_ids"] is None

        # P2 #3: Assert service was called with media_ids=None (truly unfiltered)
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["media_ids"] is None
        # Verify other forwarded parameters
        assert call_kwargs["max_items"] == body.max_items
        assert call_kwargs["dry_run"] == body.dry_run
        assert call_kwargs["only_without_ai_tags"] == body.only_without_ai_tags
        assert call_kwargs["force_suggestions"] == body.force_suggestions

    # -- Valid filter → scoped resolution --

    def test_valid_filter_resolves_scoped_ids(self):
        """Valid non-empty filter resolves to scoped media IDs.
        Service is called with exactly those IDs."""
        # DB returns 3 matching media IDs
        db, mock_query = _make_mock_db(media_rows=[10, 20, 30])
        body = CreateAITagJobRequest(
            content_class_filter=["anime"],
            max_items=50,
        )
        user = MagicMock()

        mock_job = _make_mock_job(media_ids_json=json.dumps([10, 20, 30]))

        import app.services.ai_tagging_job_service as svc
        with patch.object(svc, "create_ai_tag_job", return_value=mock_job) as mock_create, \
             patch.object(svc, "is_ai_job_active", return_value=False), \
             patch.object(svc, "start_ai_tag_job"):
            result = asyncio.run(
                _create_ai_tag_job_route(body=body, current_user=user, db=db)
            )

        # Verify the service was called with resolved media_ids
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["media_ids"] == [10, 20, 30]

    # -- only_without_ai_tags excludes all → zero match --

    def test_only_without_ai_tags_excludes_all_rejected(self):
        """When only_without_ai_tags=True and all matches are already tagged,
        the result is zero IDs → rejected with 400 (same as zero-match).
        Service must not be called."""
        from fastapi import HTTPException

        # After filtering by content_class AND excluding already-tagged, zero remain
        db, _ = _make_mock_db(media_rows=[])
        body = CreateAITagJobRequest(
            content_class_filter=["anime"],
            only_without_ai_tags=True,
            max_items=50,
        )
        user = MagicMock()

        import app.services.ai_tagging_job_service as svc
        with patch.object(svc, "create_ai_tag_job") as mock_create, \
             patch.object(svc, "is_ai_job_active", return_value=False), \
             patch.object(svc, "start_ai_tag_job"):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(_create_ai_tag_job_route(body=body, current_user=user, db=db))
            mock_create.assert_not_called()
        assert exc_info.value.status_code == 400
        assert "zero eligible media" in exc_info.value.detail

    # -- DB-side limit --

    def test_filtered_query_applies_db_side_limit(self):
        """P2 #1: The filtered query must apply .limit(max_items) DB-side."""
        db, mock_query = _make_mock_db(media_rows=[1, 2, 3])
        body = CreateAITagJobRequest(
            content_class_filter=["anime"],
            max_items=25,
        )
        user = MagicMock()

        mock_job = _make_mock_job(media_ids_json=json.dumps([1, 2, 3]))

        import app.services.ai_tagging_job_service as svc
        with patch.object(svc, "create_ai_tag_job", return_value=mock_job), \
             patch.object(svc, "is_ai_job_active", return_value=False), \
             patch.object(svc, "start_ai_tag_job"):
            asyncio.run(_create_ai_tag_job_route(body=body, current_user=user, db=db))

        # Verify .limit() was called with max_items on the query chain
        mock_query.limit.assert_called_with(25)

    # -- Invalid content_class --

    def test_invalid_content_class_returns_400(self):
        """Invalid content_class value returns HTTP 400 with valid options listed.
        Service must not be called."""
        from fastapi import HTTPException

        db, _ = _make_mock_db()
        body = CreateAITagJobRequest(
            content_class_filter=["not_a_real_class"],
            max_items=10,
        )
        user = MagicMock()

        import app.services.ai_tagging_job_service as svc
        with patch.object(svc, "create_ai_tag_job") as mock_create, \
             patch.object(svc, "is_ai_job_active", return_value=False), \
             patch.object(svc, "start_ai_tag_job"):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(_create_ai_tag_job_route(body=body, current_user=user, db=db))
            mock_create.assert_not_called()
        assert exc_info.value.status_code == 400
        assert "Invalid content_class value" in exc_info.value.detail
        assert "not_a_real_class" in exc_info.value.detail
