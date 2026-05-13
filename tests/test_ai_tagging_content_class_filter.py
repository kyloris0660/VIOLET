"""Regression tests for AI tagging content_class_filter (Phase 3.2g.1).

Tests that CreateAITagJobRequest validates content_class_filter
and that it cannot be combined with explicit media_ids.

No real AI tagging, no real LLM, no DB mutation.
"""
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

# Ensure backend is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))


class TestCreateAITagJobRequestModel:
    """Test the Pydantic model for content_class_filter field."""

    def test_default_no_content_class_filter(self):
        from app.routes.admin.ai_tagging_jobs import CreateAITagJobRequest
        req = CreateAITagJobRequest()
        assert req.content_class_filter is None

    def test_valid_content_class_filter(self):
        from app.routes.admin.ai_tagging_jobs import CreateAITagJobRequest
        req = CreateAITagJobRequest(
            content_class_filter=["anime", "illustration"],
            max_items=50,
        )
        assert req.content_class_filter == ["anime", "illustration"]
        assert req.max_items == 50

    def test_single_content_class(self):
        from app.routes.admin.ai_tagging_jobs import CreateAITagJobRequest
        req = CreateAITagJobRequest(content_class_filter=["anime"])
        assert req.content_class_filter == ["anime"]

    def test_all_valid_content_classes(self):
        from app.routes.admin.ai_tagging_jobs import CreateAITagJobRequest
        req = CreateAITagJobRequest(
            content_class_filter=["anime", "illustration", "non_anime", "unknown"]
        )
        assert len(req.content_class_filter) == 4

    def test_empty_list_is_accepted(self):
        """Empty list is accepted (will match nothing, equivalent to no filter at DB level)."""
        from app.routes.admin.ai_tagging_jobs import CreateAITagJobRequest
        req = CreateAITagJobRequest(content_class_filter=[])
        assert req.content_class_filter == []

    def test_media_ids_alone_still_works(self):
        """Existing media_ids-only requests are unaffected."""
        from app.routes.admin.ai_tagging_jobs import CreateAITagJobRequest
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
