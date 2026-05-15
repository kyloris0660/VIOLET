"""
Tests for PATCH /api/admin/tag-localization/translations/{id}
Phase 3.2j — Manual Tag Translation Correction endpoint.

10 required test cases + additional coverage:
  1. Valid display_name → 200, source='manual', status='reviewed'
  2. Valid aliases → normalized correctly
  3. needs_review=false → clears flag
  4. Empty body → 422
  5. Empty display_name string → 422
  6. Nonexistent ID → 404
  7. No admin auth → 401/403
  8. Alias normalization (dedup, trim, remove empty, remove alias==display_name)
  9. source='manual' protects from future LLM overwrite (force=False)
 10. Translation cache invalidated after PATCH
 11. aliases=[] clears existing aliases
 12. needs_review=true sets the flag
"""

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helper: create a fake TagTranslation ORM instance
# ---------------------------------------------------------------------------

def _make_translation(
    id=1,
    tag_id=100,
    canonical_name="blue_eyes",
    language="zh-CN",
    display_name="蓝眼",
    aliases_json=None,
    category="general",
    source="llm",
    status="translated",
    confidence=0.95,
    needs_review=True,
    provider="openai",
    created_at=None,
    updated_at=None,
):
    """Build a MagicMock that behaves like a TagTranslation row."""
    t = MagicMock()
    t.id = id
    t.tag_id = tag_id
    t.canonical_name = canonical_name
    t.language = language
    t.display_name = display_name
    t.aliases_json = aliases_json
    t.category = category
    t.source = source
    t.status = status
    t.confidence = confidence
    t.needs_review = needs_review
    t.provider = provider
    t.created_at = created_at or datetime(2026, 1, 1, tzinfo=timezone.utc)
    t.updated_at = updated_at or datetime(2026, 1, 1, tzinfo=timezone.utc)
    return t


def _mock_db_with_translation(trans):
    """Return a mock DB session whose query().filter().first() returns *trans*."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = trans
    return db


def _run(coro):
    """Shorthand for asyncio.run(coro)."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Import the route handler & request model under test
# ---------------------------------------------------------------------------

from app.routes.admin.tag_localization import (  # noqa: E402
    TranslationPatchRequest,
    patch_translation,
)


# ---------------------------------------------------------------------------
# Case 1: PATCH with valid display_name → 200, source='manual', status='reviewed'
# ---------------------------------------------------------------------------

class TestPatchDisplayName:
    def test_valid_display_name(self):
        trans = _make_translation(source="llm", status="translated", needs_review=True)
        db = _mock_db_with_translation(trans)
        req = TranslationPatchRequest(display_name="蓝眼睛")
        user = MagicMock()

        with patch("app.utils.search_parser.invalidate_translation_cache"):
            result = _run(patch_translation(
                translation_id=1, req=req, current_user=user, db=db,
            ))

        assert result["display_name"] == "蓝眼睛"
        assert result["source"] == "manual"
        assert result["status"] == "reviewed"
        assert result["message"] == "Translation updated"
        # Verify old values are returned for diff
        assert result["old"]["display_name"] == "蓝眼"


# ---------------------------------------------------------------------------
# Case 2: PATCH with valid aliases → normalized correctly
# ---------------------------------------------------------------------------

class TestPatchAliases:
    def test_valid_aliases_stored(self):
        trans = _make_translation(aliases_json=None)
        db = _mock_db_with_translation(trans)
        req = TranslationPatchRequest(aliases=["蓝色眼睛", "碧眼"])
        user = MagicMock()

        with patch("app.utils.search_parser.invalidate_translation_cache"):
            result = _run(patch_translation(
                translation_id=1, req=req, current_user=user, db=db,
            ))

        assert "蓝色眼睛" in result["aliases"]
        assert "碧眼" in result["aliases"]


# ---------------------------------------------------------------------------
# Case 3: PATCH needs_review=false → clears the flag
# ---------------------------------------------------------------------------

class TestPatchNeedsReview:
    def test_clear_needs_review(self):
        trans = _make_translation(needs_review=True)
        db = _mock_db_with_translation(trans)
        req = TranslationPatchRequest(needs_review=False)
        user = MagicMock()

        with patch("app.utils.search_parser.invalidate_translation_cache"):
            result = _run(patch_translation(
                translation_id=1, req=req, current_user=user, db=db,
            ))

        assert result["needs_review"] is False
        assert result["old"]["needs_review"] is True


# ---------------------------------------------------------------------------
# Case 4: Empty body (no fields) → 422
# ---------------------------------------------------------------------------

class TestPatchEmptyBody:
    def test_empty_body_raises_422(self):
        trans = _make_translation()
        db = _mock_db_with_translation(trans)
        req = TranslationPatchRequest()  # all None
        user = MagicMock()

        with pytest.raises(Exception) as exc_info:
            with patch("app.utils.search_parser.invalidate_translation_cache"):
                _run(patch_translation(
                    translation_id=1, req=req, current_user=user, db=db,
                ))

        # FastAPI HTTPException with 422
        assert exc_info.value.status_code == 422
        assert "at least one" in exc_info.value.detail.lower()


# ---------------------------------------------------------------------------
# Case 5: Empty display_name string → 422
# ---------------------------------------------------------------------------

class TestPatchEmptyDisplayName:
    def test_empty_display_name_raises_422(self):
        trans = _make_translation()
        db = _mock_db_with_translation(trans)
        req = TranslationPatchRequest(display_name="")
        user = MagicMock()

        with pytest.raises(Exception) as exc_info:
            with patch("app.utils.search_parser.invalidate_translation_cache"):
                _run(patch_translation(
                    translation_id=1, req=req, current_user=user, db=db,
                ))

        assert exc_info.value.status_code == 422

    def test_whitespace_only_display_name_raises_422(self):
        trans = _make_translation()
        db = _mock_db_with_translation(trans)
        req = TranslationPatchRequest(display_name="   ")
        user = MagicMock()

        with pytest.raises(Exception) as exc_info:
            with patch("app.utils.search_parser.invalidate_translation_cache"):
                _run(patch_translation(
                    translation_id=1, req=req, current_user=user, db=db,
                ))

        assert exc_info.value.status_code == 422


# ---------------------------------------------------------------------------
# Case 6: Nonexistent ID → 404
# ---------------------------------------------------------------------------

class TestPatchNotFound:
    def test_nonexistent_id_raises_404(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        req = TranslationPatchRequest(display_name="测试")
        user = MagicMock()

        with pytest.raises(Exception) as exc_info:
            with patch("app.utils.search_parser.invalidate_translation_cache"):
                _run(patch_translation(
                    translation_id=99999, req=req, current_user=user, db=db,
                ))

        assert exc_info.value.status_code == 404
        assert "not found" in exc_info.value.detail.lower()


# ---------------------------------------------------------------------------
# Case 7: No admin auth → endpoint requires Depends(require_admin_mode)
# ---------------------------------------------------------------------------

class TestPatchNoAuth:
    def test_endpoint_has_admin_dependency(self):
        """Verify that the PATCH endpoint declares require_admin_mode as a dependency."""
        import inspect
        sig = inspect.signature(patch_translation)
        params = sig.parameters

        assert "current_user" in params
        dep = params["current_user"].default
        # FastAPI Depends wraps the callable in a Depends object
        from fastapi import Depends as FastAPIDepends
        assert hasattr(dep, "dependency"), "current_user must use Depends()"

        from app.auth import require_admin_mode
        assert dep.dependency is require_admin_mode


# ---------------------------------------------------------------------------
# Case 8: Alias normalization — dedup, trim, remove empty, remove alias==display_name
# ---------------------------------------------------------------------------

class TestAliasNormalization:
    def test_dedup_trim_remove_empty_remove_display(self):
        trans = _make_translation(display_name="蓝眼睛")
        db = _mock_db_with_translation(trans)
        req = TranslationPatchRequest(
            display_name="蓝眼睛",
            aliases=[
                "碧眼",       # valid
                "  碧眼 ",    # duplicate after trim → removed
                "",           # empty → removed
                "  ",         # whitespace → removed
                "蓝眼睛",     # same as display_name → removed
                "蓝色眼睛",   # valid
            ],
        )
        user = MagicMock()

        with patch("app.utils.search_parser.invalidate_translation_cache"):
            result = _run(patch_translation(
                translation_id=1, req=req, current_user=user, db=db,
            ))

        assert result["aliases"] == ["碧眼", "蓝色眼睛"]


# ---------------------------------------------------------------------------
# Case 9: source='manual' protects from future LLM overwrite (force=False)
# ---------------------------------------------------------------------------

class TestManualSourceProtection:
    def test_llm_cannot_overwrite_manual(self):
        """After PATCH sets source='manual', upsert_translation(force=False, source='llm')
        must return None (blocked by priority)."""
        from app.services.tag_localization_service import SOURCE_PRIORITY

        # Verify ordering: manual < llm means manual has higher priority
        assert SOURCE_PRIORITY["manual"] < SOURCE_PRIORITY["llm"]

        # Create a mock that simulates a manual-source translation
        manual_trans = _make_translation(source="manual", status="reviewed")
        db = _mock_db_with_translation(manual_trans)

        from app.services.tag_localization_service import upsert_translation

        result = upsert_translation(
            db,
            canonical_name="blue_eyes",
            display_name="蓝色的眼睛",
            lang="zh-CN",
            source="llm",
            force=False,
        )

        assert result is None, "LLM should NOT overwrite manual-source translation"


# ---------------------------------------------------------------------------
# Case 10: Translation cache invalidated after PATCH
# ---------------------------------------------------------------------------

class TestCacheInvalidation:
    def test_cache_invalidated_on_patch(self):
        trans = _make_translation()
        db = _mock_db_with_translation(trans)
        req = TranslationPatchRequest(display_name="新译名")
        user = MagicMock()

        with patch(
            "app.utils.search_parser.invalidate_translation_cache"
        ) as mock_invalidate:
            _run(patch_translation(
                translation_id=1, req=req, current_user=user, db=db,
            ))

        mock_invalidate.assert_called_once()


# ---------------------------------------------------------------------------
# Case 11: PATCH with aliases=[] clears existing aliases
# ---------------------------------------------------------------------------

class TestPatchClearAliases:
    def test_empty_aliases_clears_existing(self):
        """PATCH with aliases=[] should set aliases_json to None, clearing all aliases."""
        trans = _make_translation(
            aliases_json=json.dumps(["碧眼", "蓝色眼睛"]),
        )
        db = _mock_db_with_translation(trans)
        req = TranslationPatchRequest(aliases=[])
        user = MagicMock()

        with patch("app.utils.search_parser.invalidate_translation_cache"):
            result = _run(patch_translation(
                translation_id=1, req=req, current_user=user, db=db,
            ))

        assert result["aliases"] == []
        assert result["old"]["aliases"] == ["碧眼", "蓝色眼睛"]
        # aliases_json should be set to None for empty list
        assert trans.aliases_json is None

    def test_all_invalid_aliases_clears(self):
        """PATCH with aliases containing only empty/whitespace strings should clear aliases."""
        trans = _make_translation(
            aliases_json=json.dumps(["碧眼"]),
        )
        db = _mock_db_with_translation(trans)
        req = TranslationPatchRequest(aliases=["", "  ", ""])
        user = MagicMock()

        with patch("app.utils.search_parser.invalidate_translation_cache"):
            result = _run(patch_translation(
                translation_id=1, req=req, current_user=user, db=db,
            ))

        assert result["aliases"] == []
        assert trans.aliases_json is None


# ---------------------------------------------------------------------------
# Case 12: PATCH needs_review=true sets the flag
# ---------------------------------------------------------------------------

class TestPatchSetNeedsReview:
    def test_set_needs_review_true(self):
        """PATCH with needs_review=true should set the flag."""
        trans = _make_translation(needs_review=False)
        db = _mock_db_with_translation(trans)
        req = TranslationPatchRequest(needs_review=True)
        user = MagicMock()

        with patch("app.utils.search_parser.invalidate_translation_cache"):
            result = _run(patch_translation(
                translation_id=1, req=req, current_user=user, db=db,
            ))

        assert result["needs_review"] is True
        assert result["old"]["needs_review"] is False
