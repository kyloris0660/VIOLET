"""Unit tests for content_class filter helper and API parameter."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from fastapi import HTTPException

from app.utils.media_helpers import VALID_CONTENT_CLASSES, apply_content_class_filter


class TestValidContentClasses:

    def test_expected_values(self):
        assert VALID_CONTENT_CLASSES == {"anime", "illustration", "non_anime", "unknown"}

    def test_is_frozen_set_like(self):
        assert isinstance(VALID_CONTENT_CLASSES, set)


class TestApplyContentClassFilter:

    @pytest.fixture(autouse=True)
    def setup_query(self):
        self.query = MagicMock()
        self.query.filter.return_value = self.query

    # --- passthrough cases ---

    def test_none_returns_query_unchanged(self):
        result = apply_content_class_filter(self.query, None)
        assert result is self.query
        self.query.filter.assert_not_called()

    def test_empty_string_returns_query_unchanged(self):
        result = apply_content_class_filter(self.query, "")
        assert result is self.query
        self.query.filter.assert_not_called()

    def test_whitespace_only_returns_query_unchanged(self):
        result = apply_content_class_filter(self.query, "   ")
        assert result is self.query
        self.query.filter.assert_not_called()

    def test_commas_only_returns_query_unchanged(self):
        result = apply_content_class_filter(self.query, ",,,")
        assert result is self.query
        self.query.filter.assert_not_called()

    # --- single valid values ---

    def test_anime_calls_filter(self):
        result = apply_content_class_filter(self.query, "anime")
        assert result is self.query
        self.query.filter.assert_called_once()

    def test_non_anime_calls_filter(self):
        result = apply_content_class_filter(self.query, "non_anime")
        assert result is self.query
        self.query.filter.assert_called_once()

    def test_illustration_calls_filter(self):
        result = apply_content_class_filter(self.query, "illustration")
        assert result is self.query
        self.query.filter.assert_called_once()

    def test_unknown_calls_filter(self):
        result = apply_content_class_filter(self.query, "unknown")
        assert result is self.query
        self.query.filter.assert_called_once()

    # --- comma-separated values ---

    def test_anime_unknown_calls_filter(self):
        result = apply_content_class_filter(self.query, "anime,unknown")
        assert result is self.query
        self.query.filter.assert_called_once()

    def test_whitespace_around_values_accepted(self):
        result = apply_content_class_filter(self.query, " anime , non_anime ")
        assert result is self.query
        self.query.filter.assert_called_once()

    def test_all_valid_combined(self):
        result = apply_content_class_filter(self.query, "anime,illustration,non_anime,unknown")
        assert result is self.query
        self.query.filter.assert_called_once()

    # --- invalid values raise 400 ---

    def test_invalid_single_value_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            apply_content_class_filter(self.query, "photo")
        assert exc_info.value.status_code == 400
        assert "photo" in exc_info.value.detail

    def test_invalid_mixed_with_valid_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            apply_content_class_filter(self.query, "anime,garbage")
        assert exc_info.value.status_code == 400
        assert "garbage" in exc_info.value.detail

    def test_multiple_invalid_values_all_listed(self):
        with pytest.raises(HTTPException) as exc_info:
            apply_content_class_filter(self.query, "foo,bar")
        assert exc_info.value.status_code == 400
        assert "foo" in exc_info.value.detail
        assert "bar" in exc_info.value.detail

    def test_case_sensitive_rejects_uppercase(self):
        with pytest.raises(HTTPException) as exc_info:
            apply_content_class_filter(self.query, "Anime")
        assert exc_info.value.status_code == 400

    # --- deduplication edge cases ---

    def test_duplicate_values_still_valid(self):
        result = apply_content_class_filter(self.query, "anime,anime")
        assert result is self.query
        self.query.filter.assert_called_once()

    def test_trailing_comma_ignored(self):
        result = apply_content_class_filter(self.query, "anime,")
        assert result is self.query
        self.query.filter.assert_called_once()

    def test_leading_comma_ignored(self):
        result = apply_content_class_filter(self.query, ",anime")
        assert result is self.query
        self.query.filter.assert_called_once()
