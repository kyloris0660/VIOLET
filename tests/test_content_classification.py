"""Unit tests for Phase 3 content classification."""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.enums import ContentClassEnum


# ---------------------------------------------------------------------------
# ContentClassEnum
# ---------------------------------------------------------------------------

class TestContentClassEnum:

    def test_values(self):
        assert ContentClassEnum.anime.value == "anime"
        assert ContentClassEnum.illustration.value == "illustration"
        assert ContentClassEnum.non_anime.value == "non_anime"
        assert ContentClassEnum.unknown.value == "unknown"

    def test_from_string(self):
        assert ContentClassEnum("anime") == ContentClassEnum.anime
        assert ContentClassEnum("non_anime") == ContentClassEnum.non_anime

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            ContentClassEnum("photo")

    def test_all_members(self):
        assert len(ContentClassEnum) == 4


# ---------------------------------------------------------------------------
# Content classifier — classify_from_predictions
# ---------------------------------------------------------------------------

class TestClassifyFromPredictions:

    @pytest.fixture(autouse=True)
    def patch_settings(self):
        with patch("app.services.content_classifier.settings") as mock_s:
            type(mock_s).CONTENT_CLASSIFICATION_ANIME_CONFIDENCE_THRESHOLD = PropertyMock(return_value=0.5)
            type(mock_s).CONTENT_CLASSIFICATION_ANIME_TAG_THRESHOLD = PropertyMock(return_value=5)
            self.mock_settings = mock_s
            yield

    def _make_media(self, content_class=None, locked=False):
        m = MagicMock()
        m.id = 1
        m.content_class = content_class
        m.content_class_locked = locked
        m.content_class_confidence = None
        m.content_class_source = None
        m.content_class_model = None
        return m

    def _make_predictions(self, n_high_conf, n_low_conf=0):
        preds = []
        for i in range(n_high_conf):
            preds.append({"name": f"tag_{i}", "confidence": 0.8, "action": "confirmed"})
        for i in range(n_low_conf):
            preds.append({"name": f"low_{i}", "confidence": 0.2, "action": "confirmed"})
        return preds

    def test_anime_classification(self):
        from app.services.content_classifier import classify_from_predictions

        media = self._make_media()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = media

        preds = self._make_predictions(n_high_conf=10)
        result = classify_from_predictions(db, 1, preds)

        assert result["content_class"] == "anime"
        assert result["high_conf_tag_count"] == 10
        assert media.content_class == ContentClassEnum.anime

    def test_non_anime_classification(self):
        from app.services.content_classifier import classify_from_predictions

        media = self._make_media()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = media

        preds = self._make_predictions(n_high_conf=2)
        result = classify_from_predictions(db, 1, preds)

        assert result["content_class"] == "non_anime"
        assert result["high_conf_tag_count"] == 2

    def test_unknown_classification_empty_predictions(self):
        from app.services.content_classifier import classify_from_predictions

        media = self._make_media()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = media

        result = classify_from_predictions(db, 1, [])

        assert result["content_class"] == "unknown"
        assert result["confidence"] == 0.0

    def test_locked_media_skipped(self):
        from app.services.content_classifier import classify_from_predictions

        media = self._make_media(content_class=ContentClassEnum.anime, locked=True)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = media

        result = classify_from_predictions(db, 1, self._make_predictions(10))

        assert result["skipped"] is True
        assert result["reason"] == "locked"

    def test_media_not_found(self):
        from app.services.content_classifier import classify_from_predictions

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        result = classify_from_predictions(db, 999, [])

        assert result["error"] == "Media not found"

    def test_dry_run_no_write(self):
        from app.services.content_classifier import classify_from_predictions

        media = self._make_media()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = media

        result = classify_from_predictions(db, 1, self._make_predictions(10), dry_run=True)

        assert result["dry_run"] is True
        assert result["content_class"] == "anime"
        assert media.content_class is None

    def test_low_confidence_tags_excluded(self):
        from app.services.content_classifier import classify_from_predictions

        media = self._make_media()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = media

        preds = self._make_predictions(n_high_conf=2, n_low_conf=20)
        result = classify_from_predictions(db, 1, preds)

        assert result["high_conf_tag_count"] == 2
        assert result["content_class"] == "non_anime"

    def test_suggestions_excluded_from_classification(self):
        """Suggestions should NOT count toward classification — only confirmed tags do.

        This matches the DB-based classify_media path which filters
        ``is_suggestion == False``.
        """
        from app.services.content_classifier import classify_from_predictions

        media = self._make_media()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = media

        preds = [
            {"name": f"tag_{i}", "confidence": 0.8, "action": "suggestion"}
            for i in range(6)
        ]
        result = classify_from_predictions(db, 1, preds)

        # 0 confirmed tags with non-empty predictions → non_anime
        assert result["content_class"] == "non_anime"
        assert result["high_conf_tag_count"] == 0
        assert result["confidence"] == 1.0

    def test_confidence_value_anime(self):
        from app.services.content_classifier import classify_from_predictions

        media = self._make_media()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = media

        preds = self._make_predictions(n_high_conf=5)
        result = classify_from_predictions(db, 1, preds)

        assert result["content_class"] == "anime"
        assert result["confidence"] == 0.5

    def test_confidence_value_non_anime(self):
        from app.services.content_classifier import classify_from_predictions

        media = self._make_media()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = media

        preds = self._make_predictions(n_high_conf=1)
        result = classify_from_predictions(db, 1, preds)

        assert result["content_class"] == "non_anime"
        assert result["confidence"] == 0.8


# ---------------------------------------------------------------------------
# Consistency: both classification paths must agree
# ---------------------------------------------------------------------------

class TestClassificationConsistency:
    """Both classify_media (DB-based) and classify_from_predictions (inline)
    must produce the same content_class for equivalent evidence."""

    @pytest.fixture(autouse=True)
    def patch_settings(self):
        with patch("app.services.content_classifier.settings") as mock_s:
            type(mock_s).CONTENT_CLASSIFICATION_ANIME_CONFIDENCE_THRESHOLD = PropertyMock(return_value=0.5)
            type(mock_s).CONTENT_CLASSIFICATION_ANIME_TAG_THRESHOLD = PropertyMock(return_value=5)
            self.mock_settings = mock_s
            yield

    def _make_media(self, content_class=None, locked=False):
        m = MagicMock()
        m.id = 1
        m.content_class = content_class
        m.content_class_locked = locked
        m.content_class_confidence = None
        m.content_class_source = None
        m.content_class_model = None
        return m

    def _make_db_tags(self, n):
        """Create mock DB tag rows (as returned by .all())."""
        rows = []
        for i in range(n):
            row = MagicMock()
            row.tag_id = i
            row.confidence = 0.8
            row.source = "ai_wd"
            row.is_suggestion = False
            rows.append(row)
        return rows

    def _make_predictions(self, n):
        """Create prediction dicts equivalent to the DB tags above."""
        return [
            {"name": f"tag_{i}", "confidence": 0.8, "action": "confirmed"}
            for i in range(n)
        ]

    @pytest.mark.parametrize("n_tags,expected_class", [
        (0, "unknown"),
        (2, "non_anime"),
        (5, "anime"),
        (10, "anime"),
    ])
    def test_both_paths_agree(self, n_tags, expected_class):
        from app.services.content_classifier import classify_media, classify_from_predictions

        # --- DB-based path ---
        media_db = self._make_media()
        db1 = MagicMock()
        db1.query.return_value.filter.return_value.first.return_value = media_db
        if n_tags == 0:
            db1.query.return_value.filter.return_value.all.return_value = []
        else:
            db1.query.return_value.filter.return_value.all.return_value = self._make_db_tags(n_tags)
        result_db = classify_media(db1, 1, dry_run=True)

        # --- Inline path ---
        media_inline = self._make_media()
        db2 = MagicMock()
        db2.query.return_value.filter.return_value.first.return_value = media_inline
        preds = self._make_predictions(n_tags) if n_tags > 0 else []
        result_inline = classify_from_predictions(db2, 1, preds, dry_run=True)

        assert result_db["content_class"] == expected_class
        assert result_inline["content_class"] == expected_class
        assert result_db["content_class"] == result_inline["content_class"]
        assert result_db["confidence"] == result_inline["confidence"]


# ---------------------------------------------------------------------------
# Content classifier — classify_media (DB-based)
# ---------------------------------------------------------------------------

class TestClassifyMedia:

    @pytest.fixture(autouse=True)
    def patch_settings(self):
        with patch("app.services.content_classifier.settings") as mock_s:
            type(mock_s).CONTENT_CLASSIFICATION_ANIME_CONFIDENCE_THRESHOLD = PropertyMock(return_value=0.5)
            type(mock_s).CONTENT_CLASSIFICATION_ANIME_TAG_THRESHOLD = PropertyMock(return_value=5)
            self.mock_settings = mock_s
            yield

    def _make_media(self, content_class=None, locked=False):
        m = MagicMock()
        m.id = 1
        m.content_class = content_class
        m.content_class_locked = locked
        m.content_class_confidence = None
        m.content_class_source = None
        m.content_class_model = None
        return m

    def test_media_not_found(self):
        from app.services.content_classifier import classify_media

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        result = classify_media(db, 999)
        assert result["error"] == "Media not found"

    def test_locked_skip(self):
        from app.services.content_classifier import classify_media

        media = self._make_media(content_class=ContentClassEnum.anime, locked=True)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = media

        result = classify_media(db, 1)
        assert result["skipped"] is True
        assert result["reason"] == "locked"
        assert result["current_class"] == "anime"

    def test_no_ai_tags_unknown(self):
        from app.services.content_classifier import classify_media

        media = self._make_media()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = media
        db.query.return_value.filter.return_value.all.return_value = []

        result = classify_media(db, 1)
        assert result["content_class"] == "unknown"
        assert result["ai_tag_count"] == 0

    def test_dry_run_no_commit(self):
        from app.services.content_classifier import classify_media

        media = self._make_media()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = media
        db.query.return_value.filter.return_value.all.return_value = []

        result = classify_media(db, 1, dry_run=True)
        assert result["dry_run"] is True
        db.commit.assert_not_called()


# ---------------------------------------------------------------------------
# Classification job service — unit tests
# ---------------------------------------------------------------------------

class TestClassificationJobService:

    def test_is_active_false_by_default(self):
        from app.services.classification_job_service import (
            _active_job_cancel,
            _active_job_lock,
            is_classification_job_active,
        )
        with _active_job_lock:
            _active_job_cancel.clear()
        assert is_classification_job_active() is False

    def test_request_cancel_sets_flag(self):
        from app.services.classification_job_service import (
            _active_job_cancel,
            _active_job_lock,
            request_classification_job_cancel,
        )
        with _active_job_lock:
            _active_job_cancel.clear()
            _active_job_cancel[42] = False

        request_classification_job_cancel(42)

        with _active_job_lock:
            assert _active_job_cancel[42] is True
            _active_job_cancel.clear()

    def test_mark_stale_classification_jobs(self):
        from app.services.classification_job_service import mark_stale_classification_jobs

        job1 = MagicMock()
        job1.status = "running"
        job2 = MagicMock()
        job2.status = "pending"

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [job1, job2]

        count = mark_stale_classification_jobs(db)

        assert count == 2
        assert job1.status == "interrupted"
        assert job2.status == "interrupted"
        assert job1.error_message == "Application stopped while this job was running"
        db.commit.assert_called_once()

    def test_mark_stale_no_stale_jobs(self):
        from app.services.classification_job_service import mark_stale_classification_jobs

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []

        count = mark_stale_classification_jobs(db)
        assert count == 0
        db.commit.assert_not_called()

    def test_create_auto_classification_disabled(self):
        from app.services.classification_job_service import create_auto_classification_job_after_scan

        with patch("app.services.classification_job_service.settings") as mock_s:
            type(mock_s).CONTENT_CLASSIFICATION_AUTO_AFTER_IMPORT = PropertyMock(return_value=False)

            result = create_auto_classification_job_after_scan(1, [1, 2, 3])
            assert result is None

    def test_create_auto_classification_feature_disabled(self):
        from app.services.classification_job_service import create_auto_classification_job_after_scan

        with patch("app.services.classification_job_service.settings") as mock_s:
            type(mock_s).CONTENT_CLASSIFICATION_AUTO_AFTER_IMPORT = PropertyMock(return_value=True)
            type(mock_s).CONTENT_CLASSIFICATION_ENABLED = PropertyMock(return_value=False)

            result = create_auto_classification_job_after_scan(1, [1, 2, 3])
            assert result is None

    def test_create_auto_classification_no_imports(self):
        from app.services.classification_job_service import create_auto_classification_job_after_scan

        with patch("app.services.classification_job_service.settings") as mock_s:
            type(mock_s).CONTENT_CLASSIFICATION_AUTO_AFTER_IMPORT = PropertyMock(return_value=True)
            type(mock_s).CONTENT_CLASSIFICATION_ENABLED = PropertyMock(return_value=True)

            result = create_auto_classification_job_after_scan(1, [])
            assert result is None

    def test_create_classification_job_respects_hard_limit(self):
        from app.services.classification_job_service import create_classification_job

        with patch("app.services.classification_job_service.settings") as mock_s:
            type(mock_s).CONTENT_CLASSIFICATION_BATCH_MAX_ITEMS = PropertyMock(return_value=50)

            db = MagicMock()
            mock_job = MagicMock()
            mock_job.id = 1
            db.add = MagicMock()
            db.commit = MagicMock()
            db.refresh = MagicMock(side_effect=lambda j: setattr(j, 'id', 1))

            job = create_classification_job(db, max_items=999)

            assert job.max_items == 50


# ---------------------------------------------------------------------------
# Search parser — class: meta filter
# ---------------------------------------------------------------------------

class TestSearchParserClassFilter:

    def test_parse_class_anime(self):
        from app.utils.search_parser import parse_search_query

        result = parse_search_query("class:anime")
        assert "class" in result["meta"] or "content_class" in result["meta"]
        items = result["meta"].get("class", []) + result["meta"].get("content_class", [])
        assert len(items) == 1
        assert items[0]["value"] == "anime"
        assert items[0]["negated"] is False

    def test_parse_class_negated(self):
        from app.utils.search_parser import parse_search_query

        result = parse_search_query("-class:non_anime")
        items = result["meta"].get("class", []) + result["meta"].get("content_class", [])
        assert len(items) == 1
        assert items[0]["value"] == "non_anime"
        assert items[0]["negated"] is True

    def test_parse_class_none(self):
        from app.utils.search_parser import parse_search_query

        result = parse_search_query("class:none")
        items = result["meta"].get("class", []) + result["meta"].get("content_class", [])
        assert len(items) == 1
        assert items[0]["value"] == "none"

    def test_parse_content_class_alias(self):
        from app.utils.search_parser import parse_search_query

        result = parse_search_query("content_class:illustration")
        items = result["meta"].get("class", []) + result["meta"].get("content_class", [])
        assert len(items) == 1
        assert items[0]["value"] == "illustration"

    def test_parse_class_with_tags(self):
        from app.utils.search_parser import parse_search_query

        result = parse_search_query("blue_eyes class:anime long_hair")
        items = result["meta"].get("class", []) + result["meta"].get("content_class", [])
        assert len(items) == 1
        assert items[0]["value"] == "anime"
        assert len(result["tags"]["include"]) == 2


# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------

class TestContentClassificationConfig:

    def test_default_disabled(self):
        with patch.dict("os.environ", {}, clear=False):
            from importlib import reload
            import app.config
            reload(app.config)
            s = app.config.Settings()
            assert s.CONTENT_CLASSIFICATION_ENABLED is False

    def test_default_batch_max(self):
        with patch.dict("os.environ", {}, clear=False):
            from importlib import reload
            import app.config
            reload(app.config)
            s = app.config.Settings()
            assert s.CONTENT_CLASSIFICATION_BATCH_MAX_ITEMS == 100

    def test_default_auto_after_import_off(self):
        with patch.dict("os.environ", {}, clear=False):
            from importlib import reload
            import app.config
            reload(app.config)
            s = app.config.Settings()
            assert s.CONTENT_CLASSIFICATION_AUTO_AFTER_IMPORT is False

    def test_default_thresholds(self):
        with patch.dict("os.environ", {}, clear=False):
            from importlib import reload
            import app.config
            reload(app.config)
            s = app.config.Settings()
            assert s.CONTENT_CLASSIFICATION_ANIME_TAG_THRESHOLD == 5
            assert s.CONTENT_CLASSIFICATION_ANIME_CONFIDENCE_THRESHOLD == 0.5
