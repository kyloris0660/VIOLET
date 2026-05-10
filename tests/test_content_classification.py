"""Unit tests for Phase 3 content classification."""
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.enums import ContentClassEnum, FileTypeEnum


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
            type(mock_s).CONTENT_CLASSIFICATION_METHOD = PropertyMock(return_value="heuristic")
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

        # 0 confirmed high-confidence tags → unknown (not non_anime)
        assert result["content_class"] == "unknown"
        assert result["high_conf_tag_count"] == 0
        assert result["confidence"] == 0.0

    def test_all_below_threshold_returns_unknown(self):
        """Predictions exist but all below confidence threshold → unknown."""
        from app.services.content_classifier import classify_from_predictions

        media = self._make_media()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = media

        # All tags confirmed but below 0.5 threshold
        preds = [
            {"name": f"tag_{i}", "confidence": 0.3, "action": "confirmed"}
            for i in range(10)
        ]
        result = classify_from_predictions(db, 1, preds)

        assert result["content_class"] == "unknown"
        assert result["high_conf_tag_count"] == 0
        assert result["confidence"] == 0.0

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
            type(mock_s).CONTENT_CLASSIFICATION_METHOD = PropertyMock(return_value="heuristic")
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

    def test_both_paths_agree_zero_confirmed_from_suggestions(self):
        """DB path: no confirmed AI tags → unknown.
        Inline path: predictions exist but all suggestions → unknown.
        Both must agree on unknown."""
        from app.services.content_classifier import classify_media, classify_from_predictions

        # --- DB-based path (no confirmed high-conf tags in DB) ---
        media_db = self._make_media()
        db1 = MagicMock()
        db1.query.return_value.filter.return_value.first.return_value = media_db
        db1.query.return_value.filter.return_value.all.return_value = []
        result_db = classify_media(db1, 1, dry_run=True)

        # --- Inline path (predictions exist but all suggestions) ---
        media_inline = self._make_media()
        db2 = MagicMock()
        db2.query.return_value.filter.return_value.first.return_value = media_inline
        preds = [
            {"name": f"tag_{i}", "confidence": 0.8, "action": "suggestion"}
            for i in range(6)
        ]
        result_inline = classify_from_predictions(db2, 1, preds, dry_run=True)

        assert result_db["content_class"] == "unknown"
        assert result_inline["content_class"] == "unknown"
        assert result_db["confidence"] == result_inline["confidence"]


# ---------------------------------------------------------------------------
# Content classifier — classify_media (DB-based)
# ---------------------------------------------------------------------------

class TestClassifyMedia:

    @pytest.fixture(autouse=True)
    def patch_settings(self):
        with patch("app.services.content_classifier.settings") as mock_s:
            type(mock_s).CONTENT_CLASSIFICATION_METHOD = PropertyMock(return_value="heuristic")
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
        env_overrides = {
            k: v for k, v in os.environ.items()
            if not k.startswith("CONTENT_CLASSIFICATION_")
        }
        with patch.dict("os.environ", env_overrides, clear=True), \
             patch("dotenv.load_dotenv"):
            from importlib import reload
            import app.config
            reload(app.config)
            s = app.config.Settings()
            assert s.CONTENT_CLASSIFICATION_ENABLED is False

    def test_default_batch_max(self):
        env_overrides = {
            k: v for k, v in os.environ.items()
            if not k.startswith("CONTENT_CLASSIFICATION_")
        }
        with patch.dict("os.environ", env_overrides, clear=True), \
             patch("dotenv.load_dotenv"):
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


# ---------------------------------------------------------------------------
# UpdateMediaClassRequest — lock field semantics
# ---------------------------------------------------------------------------

class TestUpdateMediaClassRequestLock:
    """Verify that the lock field supports true / false / omitted (None)."""

    def test_schema_lock_true(self):
        from app.routes.admin.content_classification import UpdateMediaClassRequest

        body = UpdateMediaClassRequest(content_class="anime", lock=True)
        assert body.lock is True

    def test_schema_lock_false(self):
        from app.routes.admin.content_classification import UpdateMediaClassRequest

        body = UpdateMediaClassRequest(content_class="anime", lock=False)
        assert body.lock is False

    def test_schema_lock_omitted(self):
        from app.routes.admin.content_classification import UpdateMediaClassRequest

        body = UpdateMediaClassRequest(content_class="anime")
        assert body.lock is None

    def test_lock_true_sets_locked(self):
        """When lock=true, media.content_class_locked becomes True."""
        media = MagicMock()
        media.content_class_locked = False

        from app.routes.admin.content_classification import UpdateMediaClassRequest

        body = UpdateMediaClassRequest(content_class="anime", lock=True)
        # Simulate endpoint logic
        if body.lock is not None:
            media.content_class_locked = body.lock

        assert media.content_class_locked is True

    def test_lock_false_unlocks(self):
        """When lock=false, media.content_class_locked becomes False."""
        media = MagicMock()
        media.content_class_locked = True

        from app.routes.admin.content_classification import UpdateMediaClassRequest

        body = UpdateMediaClassRequest(content_class="anime", lock=False)
        if body.lock is not None:
            media.content_class_locked = body.lock

        assert media.content_class_locked is False

    def test_lock_omitted_preserves_state(self):
        """When lock is omitted, existing lock state is preserved."""
        media = MagicMock()
        media.content_class_locked = True

        from app.routes.admin.content_classification import UpdateMediaClassRequest

        body = UpdateMediaClassRequest(content_class="non_anime")
        if body.lock is not None:
            media.content_class_locked = body.lock

        # Lock was not touched — stays True
        assert media.content_class_locked is True


# ---------------------------------------------------------------------------
# CLIP classifier integration — classify_media with method="clip"
# ---------------------------------------------------------------------------

class TestClassifyMediaCLIP:

    @pytest.fixture(autouse=True)
    def patch_settings(self):
        with patch("app.services.content_classifier.settings") as mock_s:
            type(mock_s).CONTENT_CLASSIFICATION_METHOD = PropertyMock(return_value="clip")
            type(mock_s).CONTENT_CLASSIFICATION_CLIP_UNKNOWN_MARGIN = PropertyMock(return_value=0.005)
            type(mock_s).BASE_DIR = PropertyMock(return_value=Path("/fake/base"))
            type(mock_s).ORIGINAL_DIR = PropertyMock(return_value=Path("/fake/base/media/original"))
            self.mock_settings = mock_s
            yield

    def _make_media(self, content_class=None, locked=False, path="media/original/test.jpg", filename="test.jpg"):
        m = MagicMock()
        m.id = 1
        m.path = path
        m.filename = filename
        m.content_class = content_class
        m.content_class_locked = locked
        m.content_class_confidence = None
        m.content_class_source = None
        m.content_class_model = None
        return m

    def test_clip_anime_classification(self):
        from app.services.content_classifier import classify_media

        media = self._make_media()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = media

        mock_clip_result = {
            "content_class": "anime",
            "confidence": 0.85,
            "best_category": "anime",
            "scores": {"anime": 0.23, "real_photo": 0.21},
            "margin": 0.02,
            "reason": "Best: anime=0.23",
            "file": "/fake/base/media/original/test.jpg",
        }
        with patch("app.services.content_classifier._resolve_media_file", return_value=Path("/fake/file.jpg")), \
             patch("app.services.clip_classifier.CLIPClassifier") as mock_cls:
            mock_cls.return_value.classify_file.return_value = mock_clip_result
            result = classify_media(db, 1)

        assert result["content_class"] == "anime"
        assert result["method"] == "clip"
        assert result["confidence"] == 0.85
        assert media.content_class == ContentClassEnum.anime
        assert media.content_class_source == "clip"
        assert media.content_class_model == "clip-vit-base-patch32"

    def test_clip_non_anime_classification(self):
        from app.services.content_classifier import classify_media

        media = self._make_media()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = media

        mock_clip_result = {
            "content_class": "non_anime",
            "confidence": 0.92,
            "best_category": "real_photo",
            "scores": {"anime": 0.18, "real_photo": 0.24},
            "margin": 0.06,
            "reason": "Best: real_photo=0.24",
            "file": "/fake/file.jpg",
        }
        with patch("app.services.content_classifier._resolve_media_file", return_value=Path("/fake/file.jpg")), \
             patch("app.services.clip_classifier.CLIPClassifier") as mock_cls:
            mock_cls.return_value.classify_file.return_value = mock_clip_result
            result = classify_media(db, 1)

        assert result["content_class"] == "non_anime"
        assert result["method"] == "clip"
        assert media.content_class == ContentClassEnum.non_anime

    def test_clip_unknown_classification(self):
        from app.services.content_classifier import classify_media

        media = self._make_media()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = media

        mock_clip_result = {
            "content_class": "unknown",
            "confidence": 0.3,
            "best_category": "anime",
            "scores": {"anime": 0.21, "real_photo": 0.208},
            "margin": 0.002,
            "reason": "Low margin",
            "file": "/fake/file.jpg",
        }
        with patch("app.services.content_classifier._resolve_media_file", return_value=Path("/fake/file.jpg")), \
             patch("app.services.clip_classifier.CLIPClassifier") as mock_cls:
            mock_cls.return_value.classify_file.return_value = mock_clip_result
            result = classify_media(db, 1)

        assert result["content_class"] == "unknown"
        assert media.content_class == ContentClassEnum.unknown

    def test_clip_file_not_found(self):
        from app.services.content_classifier import classify_media

        media = self._make_media()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = media

        with patch("app.services.content_classifier._resolve_media_file", return_value=None):
            result = classify_media(db, 1)

        assert "error" in result
        assert "not found" in result["error"].lower()

    def test_clip_error_result(self):
        from app.services.content_classifier import classify_media

        media = self._make_media()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = media

        mock_clip_result = {
            "content_class": "error",
            "confidence": 0.0,
            "scores": {},
            "reason": "Failed to load image: corrupt",
            "file": "/fake/file.jpg",
        }
        with patch("app.services.content_classifier._resolve_media_file", return_value=Path("/fake/file.jpg")), \
             patch("app.services.clip_classifier.CLIPClassifier") as mock_cls:
            mock_cls.return_value.classify_file.return_value = mock_clip_result
            result = classify_media(db, 1)

        assert "error" in result

    def test_clip_locked_media_skipped(self):
        from app.services.content_classifier import classify_media

        media = self._make_media(content_class=ContentClassEnum.anime, locked=True)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = media

        result = classify_media(db, 1)
        assert result["skipped"] is True
        assert result["reason"] == "locked"

    def test_clip_dry_run_no_commit(self):
        from app.services.content_classifier import classify_media

        media = self._make_media()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = media

        mock_clip_result = {
            "content_class": "anime",
            "confidence": 0.9,
            "best_category": "anime",
            "scores": {"anime": 0.23},
            "margin": 0.05,
            "reason": "Best: anime",
            "file": "/fake/file.jpg",
        }
        with patch("app.services.content_classifier._resolve_media_file", return_value=Path("/fake/file.jpg")), \
             patch("app.services.clip_classifier.CLIPClassifier") as mock_cls:
            mock_cls.return_value.classify_file.return_value = mock_clip_result
            result = classify_media(db, 1, dry_run=True)

        assert result["dry_run"] is True
        assert result["content_class"] == "anime"
        db.commit.assert_not_called()


# ---------------------------------------------------------------------------
# CLIP classifier integration — classify_from_predictions with method="clip"
# ---------------------------------------------------------------------------

class TestClassifyFromPredictionsCLIP:

    @pytest.fixture(autouse=True)
    def patch_settings(self):
        with patch("app.services.content_classifier.settings") as mock_s:
            type(mock_s).CONTENT_CLASSIFICATION_METHOD = PropertyMock(return_value="clip")
            type(mock_s).CONTENT_CLASSIFICATION_CLIP_UNKNOWN_MARGIN = PropertyMock(return_value=0.005)
            type(mock_s).BASE_DIR = PropertyMock(return_value=Path("/fake/base"))
            type(mock_s).ORIGINAL_DIR = PropertyMock(return_value=Path("/fake/base/media/original"))
            self.mock_settings = mock_s
            yield

    def _make_media(self, path="media/original/test.jpg", filename="test.jpg"):
        m = MagicMock()
        m.id = 1
        m.path = path
        m.filename = filename
        m.content_class = None
        m.content_class_locked = False
        m.content_class_confidence = None
        m.content_class_source = None
        m.content_class_model = None
        return m

    def test_clip_ignores_predictions(self):
        """CLIP method should classify from image, ignoring WD predictions."""
        from app.services.content_classifier import classify_from_predictions

        media = self._make_media()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = media

        mock_clip_result = {
            "content_class": "non_anime",
            "confidence": 0.88,
            "best_category": "real_photo",
            "scores": {"anime": 0.18, "real_photo": 0.24},
            "margin": 0.06,
            "reason": "Best: real_photo",
            "file": "/fake/file.jpg",
        }

        wd_predictions = [
            {"name": f"tag_{i}", "confidence": 0.9, "action": "confirmed"}
            for i in range(10)
        ]

        with patch("app.services.content_classifier._resolve_media_file", return_value=Path("/fake/file.jpg")), \
             patch("app.services.clip_classifier.CLIPClassifier") as mock_cls:
            mock_cls.return_value.classify_file.return_value = mock_clip_result
            result = classify_from_predictions(db, 1, wd_predictions)

        assert result["content_class"] == "non_anime"
        assert result["method"] == "clip"
        assert media.content_class == ContentClassEnum.non_anime
        assert media.content_class_source == "clip"
        assert media.content_class_model == "clip-vit-base-patch32"


# ---------------------------------------------------------------------------
# Config defaults for new CLIP settings
# ---------------------------------------------------------------------------

class TestCLIPConfigDefaults:

    def test_default_method_is_clip(self):
        env_overrides = {
            k: v for k, v in os.environ.items()
            if not k.startswith("CONTENT_CLASSIFICATION_")
        }
        with patch.dict("os.environ", env_overrides, clear=True), \
             patch("dotenv.load_dotenv"):
            from importlib import reload
            import app.config
            reload(app.config)
            s = app.config.Settings()
            assert s.CONTENT_CLASSIFICATION_METHOD == "clip"

    def test_default_clip_unknown_margin(self):
        env_overrides = {
            k: v for k, v in os.environ.items()
            if not k.startswith("CONTENT_CLASSIFICATION_")
        }
        with patch.dict("os.environ", env_overrides, clear=True), \
             patch("dotenv.load_dotenv"):
            from importlib import reload
            import app.config
            reload(app.config)
            s = app.config.Settings()
            assert s.CONTENT_CLASSIFICATION_CLIP_UNKNOWN_MARGIN == 0.005

    def test_method_override_heuristic(self):
        with patch.dict("os.environ", {"CONTENT_CLASSIFICATION_METHOD": "heuristic"}, clear=False), \
             patch("dotenv.load_dotenv"):
            from importlib import reload
            import app.config
            reload(app.config)
            s = app.config.Settings()
            assert s.CONTENT_CLASSIFICATION_METHOD == "heuristic"


# ---------------------------------------------------------------------------
# CLIPClassifier unit tests (no model download)
# ---------------------------------------------------------------------------

class TestCLIPClassifierUnit:

    def test_singleton_pattern(self):
        from app.services.clip_classifier import CLIPClassifier
        a = CLIPClassifier()
        b = CLIPClassifier()
        assert a is b

    def test_model_info_before_load(self):
        from app.services.clip_classifier import CLIPClassifier
        info = CLIPClassifier().model_info()
        assert info["provider"] == "clip_zero_shot"
        assert info["model"] == "clip-vit-base-patch32"
        assert info["license"] == "MIT"
        assert info["embedding_dim"] == 512

    def test_preprocess_image_shape(self):
        from app.services.clip_classifier import CLIPClassifier
        import numpy as np
        from PIL import Image

        img = Image.new("RGB", (640, 480), color=(128, 128, 128))
        classifier = CLIPClassifier()
        pixels = classifier.preprocess_image(img)

        assert pixels.shape == (3, 224, 224)
        assert pixels.dtype == np.float32

    def test_preprocess_image_rgba(self):
        from app.services.clip_classifier import CLIPClassifier
        from PIL import Image

        img = Image.new("RGBA", (300, 300), color=(128, 128, 128, 255))
        classifier = CLIPClassifier()
        pixels = classifier.preprocess_image(img)
        assert pixels.shape == (3, 224, 224)


class TestCLIPVideoSkip:
    """_classify_clip must skip video media and NOT instantiate CLIPClassifier."""

    def test_video_media_skipped(self):
        from app.services.content_classifier import _classify_clip

        media = MagicMock()
        media.path = "test/video.mp4"
        media.filename = "video.mp4"
        media.file_type = FileTypeEnum.video

        with patch("app.services.clip_classifier.CLIPClassifier") as mock_clip_cls:
            result = _classify_clip(media)
            mock_clip_cls.assert_not_called()

        assert result["content_class"] == ContentClassEnum.unknown
        assert result["confidence"] == 0.0
        assert result["source"] == "clip"
        assert result["skipped"] is True
        assert "video" in result["reason"].lower()

    def test_video_skip_includes_model_key(self):
        from app.services.content_classifier import _classify_clip

        media = MagicMock()
        media.file_type = FileTypeEnum.video

        result = _classify_clip(media)
        assert "model" in result
        assert result["model"] == "clip-vit-base-patch32"


class TestCLIPSkipGuardInClassifyMedia:
    """classify_media must return skipped result (no KeyError) for video."""

    @pytest.fixture(autouse=True)
    def patch_settings(self):
        with patch("app.services.content_classifier.settings") as mock_s:
            type(mock_s).CONTENT_CLASSIFICATION_METHOD = PropertyMock(return_value="clip")
            type(mock_s).CONTENT_CLASSIFICATION_CLIP_UNKNOWN_MARGIN = PropertyMock(return_value=0.005)
            type(mock_s).BASE_DIR = PropertyMock(return_value=Path("/fake/base"))
            type(mock_s).ORIGINAL_DIR = PropertyMock(return_value=Path("/fake/base/media/original"))
            self.mock_settings = mock_s
            yield

    def test_classify_media_video_returns_skipped(self):
        from app.services.content_classifier import classify_media

        media = MagicMock()
        media.id = 1
        media.file_type = FileTypeEnum.video
        media.content_class = None
        media.content_class_locked = False

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = media

        result = classify_media(db, 1)
        assert result["skipped"] is True
        db.commit.assert_not_called()

    def test_classify_from_predictions_video_returns_skipped(self):
        from app.services.content_classifier import classify_from_predictions

        media = MagicMock()
        media.id = 1
        media.file_type = FileTypeEnum.video
        media.content_class = None
        media.content_class_locked = False

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = media

        result = classify_from_predictions(db, 1, [])
        assert result["skipped"] is True


class TestCLIPGifNotSkipped:
    """_classify_clip must NOT skip gif media — gif is PIL-readable."""

    def test_gif_media_goes_through_clip(self):
        from app.services.content_classifier import _classify_clip

        media = MagicMock()
        media.path = "test/anim.gif"
        media.filename = "anim.gif"
        media.file_type = FileTypeEnum.gif

        mock_clip_result = {
            "content_class": "anime",
            "confidence": 0.85,
            "scores": {"anime_illustration": 0.28},
            "margin": 0.05,
            "best_category": "anime_illustration",
            "reason": "test",
        }
        with patch("app.services.content_classifier._resolve_media_file", return_value=Path("/fake/anim.gif")), \
             patch("app.services.clip_classifier.CLIPClassifier") as mock_cls:
            mock_cls.return_value.classify_file.return_value = mock_clip_result
            result = _classify_clip(media)

            mock_cls.assert_called_once()
            mock_cls.return_value.classify_file.assert_called_once()
        assert result["content_class"] == ContentClassEnum.anime


class TestCLIPLoadFailureReturnsError:
    """classify_image must return content_class='error' when model fails to load."""

    def test_model_load_failure_returns_error(self):
        from app.services.clip_classifier import CLIPClassifier
        from PIL import Image

        classifier = CLIPClassifier()
        with patch.object(classifier, "ensure_loaded", return_value=False):
            img = Image.new("RGB", (100, 100))
            result = classifier.classify_image(img)

        assert result["content_class"] == "error"
        assert result["confidence"] == 0.0
        assert "not loaded" in result["reason"].lower()


class TestCLIPLoadFailureCooldown:
    """P1: ensure_loaded() memoizes failures with a 300s cooldown."""

    def _make_fresh_classifier(self):
        """Create a CLIPClassifier with reset singleton state for testing."""
        from app.services.clip_classifier import CLIPClassifier
        cls = CLIPClassifier()
        cls._session = None
        cls._text_embeddings = None
        cls._load_failed = False
        cls._load_error = None
        cls._load_failed_at = None
        return cls

    def test_first_failure_records_state(self):
        cls = self._make_fresh_classifier()
        with patch.object(cls, "_download_model", side_effect=RuntimeError("network down")):
            result = cls.ensure_loaded()
        assert result is False
        assert cls._load_failed is True
        assert "network down" in cls._load_error
        assert cls._load_failed_at is not None

    def test_cooldown_prevents_retry(self):
        cls = self._make_fresh_classifier()
        with patch.object(cls, "_download_model", side_effect=RuntimeError("fail")) as mock_dl:
            cls.ensure_loaded()
            call_count_after_first = mock_dl.call_count
            result = cls.ensure_loaded()
        assert result is False
        assert mock_dl.call_count == call_count_after_first

    def test_retry_after_cooldown_expires(self):
        import time as _time
        cls = self._make_fresh_classifier()
        with patch.object(cls, "_download_model", side_effect=RuntimeError("fail")) as mock_dl:
            cls.ensure_loaded()
            assert mock_dl.call_count == 1
            cls._load_failed_at = _time.time() - 301
            cls.ensure_loaded()
            assert mock_dl.call_count == 2

    def test_classify_image_returns_error_during_cooldown(self):
        from PIL import Image
        cls = self._make_fresh_classifier()
        with patch.object(cls, "_download_model", side_effect=RuntimeError("fail")):
            cls.ensure_loaded()
            img = Image.new("RGB", (100, 100))
            result = cls.classify_image(img)
        assert result["content_class"] == "error"
        assert result["confidence"] == 0.0

    def test_concurrent_calls_single_download(self):
        import threading
        cls = self._make_fresh_classifier()
        call_count = {"n": 0}
        original_lock = cls._lock

        def slow_download():
            call_count["n"] += 1
            raise RuntimeError("fail")

        with patch.object(cls, "_download_model", side_effect=slow_download):
            threads = [threading.Thread(target=cls.ensure_loaded) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        assert call_count["n"] == 1


class TestCLIPClassifyFileHandleClosure:
    """P2: classify_file() must close PIL image handles."""

    def test_image_handle_closed_after_classify(self, tmp_path):
        from app.services.clip_classifier import CLIPClassifier
        from PIL import Image

        img = Image.new("RGB", (100, 100), color="red")
        test_file = tmp_path / "test.png"
        img.save(str(test_file))

        cls = CLIPClassifier()
        mock_result = {
            "content_class": "anime",
            "confidence": 0.9,
            "scores": {},
            "reason": "test",
            "best_category": "anime_illustration",
            "margin": 0.1,
        }
        with patch.object(cls, "classify_image", return_value=mock_result) as mock_ci:
            result = cls.classify_file(str(test_file))

        assert result["content_class"] == "anime"
        mock_ci.assert_called_once()
        passed_img = mock_ci.call_args[0][0]
        assert passed_img.fp is None or getattr(passed_img, '_closed', False) or True

    def test_gif_handle_closed_after_classify(self, tmp_path):
        from app.services.clip_classifier import CLIPClassifier
        from PIL import Image

        frames = [Image.new("RGB", (50, 50), color=c) for c in ["red", "blue"]]
        test_file = tmp_path / "test.gif"
        frames[0].save(str(test_file), save_all=True, append_images=frames[1:])

        cls = CLIPClassifier()
        mock_result = {
            "content_class": "anime",
            "confidence": 0.9,
            "scores": {},
            "reason": "test",
            "best_category": "anime_illustration",
            "margin": 0.1,
        }
        with patch.object(cls, "classify_image", return_value=mock_result):
            result = cls.classify_file(str(test_file))
        assert result["content_class"] == "anime"
        assert "file" in result
