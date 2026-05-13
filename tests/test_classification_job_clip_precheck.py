"""Regression tests for CLIP readiness pre-check in classification_job_service.

Verifies that:
- Video-only jobs skip the CLIP pre-check (P2 fix).
- Image-containing jobs fail early when CLIP is unavailable.
- Mixed jobs (image + video) still fail early.
- Successful CLIP loads proceed normally.

All tests mock the database and CLIP classifier — no real model loading.
"""
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# Helpers: lightweight fakes for Media, ClassificationJob, and DB session
# ---------------------------------------------------------------------------

class FakeFileType:
    """Mimics FileTypeEnum values."""
    def __init__(self, value):
        self.value = value

    def __eq__(self, other):
        if isinstance(other, FakeFileType):
            return self.value == other.value
        return self.value == getattr(other, "value", other)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.value)


VIDEO = FakeFileType("video")
IMAGE = FakeFileType("image")
GIF = FakeFileType("gif")


class FakeMedia:
    """Lightweight Media stand-in with id and file_type."""
    def __init__(self, id, file_type):
        self.id = id
        self.file_type = file_type
        self.content_class = None
        self.content_class_locked = False
        self.filename = f"media_{id}.jpg"
        self.path = f"originals/media_{id}.jpg"


class FakeJob:
    """Lightweight ClassificationJob stand-in."""
    def __init__(self, id=1, media_ids=None, max_items=100,
                 only_unclassified=True, force_reclassify=False):
        self.id = id
        self.status = "pending"
        self.trigger_source = "manual"
        self.scan_job_id = None
        self.media_ids_json = json.dumps(media_ids) if media_ids else None
        self.max_items = max_items
        self.only_unclassified = only_unclassified
        self.force_reclassify = force_reclassify
        self.processed = 0
        self.classified_anime = 0
        self.classified_non_anime = 0
        self.classified_unknown = 0
        self.failed = 0
        self.failed_items_json = None
        self.error_message = None
        self.created_at = datetime.now(timezone.utc)
        self.started_at = None
        self.finished_at = None


class FakeQuery:
    """Minimal chainable query mock."""
    def __init__(self, results=None):
        self._results = results or []

    def get(self, id):
        for r in self._results:
            if getattr(r, "id", None) == id:
                return r
        return None

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args):
        return self

    def all(self):
        return self._results


class FakeDB:
    """Minimal DB session mock."""
    def __init__(self, job, media_items=None):
        self._job = job
        self._media_items = media_items or []
        self._committed = False

    def query(self, model_cls):
        # Check by class name since we can't import real models
        cls_name = getattr(model_cls, "__name__", str(model_cls))
        if cls_name == "ClassificationJob" or cls_name == "FakeJob":
            return FakeQuery([self._job])
        if cls_name == "Media" or cls_name == "FakeMedia":
            return FakeQuery(self._media_items)
        # For Media.id queries (column attribute)
        return FakeQuery(self._media_items)

    def commit(self):
        self._committed = True

    def rollback(self):
        pass

    def close(self):
        pass

    def refresh(self, obj):
        pass


# ---------------------------------------------------------------------------
# Patches: intercept the real module imports inside run_classification_job
# ---------------------------------------------------------------------------

def _make_patches(db, media_items, clip_ensure_loaded_rv=True,
                  clip_load_error=None, clip_import_error=False,
                  classify_results=None):
    """Build the set of patches needed to run run_classification_job in isolation."""
    patches = {}

    # Patch SessionLocal to return our fake DB
    patches["session_local"] = patch(
        "backend.app.services.classification_job_service.SessionLocal",
        return_value=db,
    )

    # Patch the classify_media function
    if classify_results is None:
        classify_results = {}

    def fake_classify(db_session, media_id):
        if media_id in classify_results:
            return classify_results[media_id]
        return {"content_class": "unknown", "confidence": 0.0}

    patches["classify"] = patch(
        "backend.app.services.classification_job_service.classify_media",
        side_effect=fake_classify,
    )

    return patches


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


class TestClipPrecheckVideoOnly:
    """P2 regression: video-only jobs must NOT fail at CLIP precheck."""

    def test_video_only_skips_clip_precheck(self):
        """Video-only candidates should bypass CLIP pre-check entirely."""
        from app.enums import FileTypeEnum

        job = FakeJob(id=42, media_ids=[1, 2, 3])
        videos = [
            FakeMedia(1, FileTypeEnum.video),
            FakeMedia(2, FileTypeEnum.video),
            FakeMedia(3, FileTypeEnum.video),
        ]

        db = FakeDB(job, videos)

        # CLIP is unavailable (would fail if pre-check runs)
        mock_clip_cls = MagicMock()
        mock_clip_cls.return_value.ensure_loaded.return_value = False
        mock_clip_cls.return_value._load_error = "Model not found"

        with patch("app.database.SessionLocal", return_value=db), \
             patch("app.config.settings") as mock_settings, \
             patch("app.services.content_classifier.classify_media") as mock_classify:
            mock_settings.CONTENT_CLASSIFICATION_METHOD = "clip"
            mock_settings.CONTENT_CLASSIFICATION_BATCH_MAX_ITEMS = 1000

            # classify_media returns skip for videos
            mock_classify.return_value = {
                "skipped": True,
                "reason": "Skipped: CLIP does not support video files",
            }

            from app.services.classification_job_service import run_classification_job
            run_classification_job(42)

        # The job should complete, NOT fail at pre-check
        assert job.status == "completed"
        assert job.error_message is None or "CLIP readiness" not in (job.error_message or "")

    def test_image_candidates_fail_early_when_clip_unavailable(self):
        """Image candidates should trigger CLIP pre-check and fail early."""
        from app.enums import FileTypeEnum

        job = FakeJob(id=43, media_ids=[10, 11])
        images = [
            FakeMedia(10, FileTypeEnum.image),
            FakeMedia(11, FileTypeEnum.image),
        ]

        db = FakeDB(job, images)

        mock_clip_instance = MagicMock()
        mock_clip_instance.ensure_loaded.return_value = False
        mock_clip_instance._load_error = "ONNX model not cached"
        mock_clip_cls = MagicMock(return_value=mock_clip_instance)

        with patch("app.database.SessionLocal", return_value=db), \
             patch("app.config.settings") as mock_settings, \
             patch.dict("sys.modules", {
                 "app.services.clip_classifier": MagicMock(CLIPClassifier=mock_clip_cls),
             }):
            mock_settings.CONTENT_CLASSIFICATION_METHOD = "clip"
            mock_settings.CONTENT_CLASSIFICATION_BATCH_MAX_ITEMS = 1000

            from app.services.classification_job_service import run_classification_job
            run_classification_job(43)

        assert job.status == "failed"
        assert "CLIP readiness pre-check failed" in job.error_message
        assert "ONNX model not cached" in job.error_message

    def test_mixed_candidates_fail_early_when_clip_unavailable(self):
        """Mixed job (video + image) should still fail early if CLIP unavailable."""
        from app.enums import FileTypeEnum

        job = FakeJob(id=44, media_ids=[20, 21, 22])
        media = [
            FakeMedia(20, FileTypeEnum.video),
            FakeMedia(21, FileTypeEnum.image),
            FakeMedia(22, FileTypeEnum.video),
        ]

        db = FakeDB(job, media)

        mock_clip_instance = MagicMock()
        mock_clip_instance.ensure_loaded.return_value = False
        mock_clip_instance._load_error = "Cache miss"
        mock_clip_cls = MagicMock(return_value=mock_clip_instance)

        with patch("app.database.SessionLocal", return_value=db), \
             patch("app.config.settings") as mock_settings, \
             patch.dict("sys.modules", {
                 "app.services.clip_classifier": MagicMock(CLIPClassifier=mock_clip_cls),
             }):
            mock_settings.CONTENT_CLASSIFICATION_METHOD = "clip"
            mock_settings.CONTENT_CLASSIFICATION_BATCH_MAX_ITEMS = 1000

            from app.services.classification_job_service import run_classification_job
            run_classification_job(44)

        assert job.status == "failed"
        assert "CLIP readiness pre-check failed" in job.error_message

    def test_clip_available_proceeds_normally(self):
        """When CLIP loads successfully, job should proceed to per-item processing."""
        from app.enums import FileTypeEnum

        job = FakeJob(id=45, media_ids=[30, 31])
        images = [
            FakeMedia(30, FileTypeEnum.image),
            FakeMedia(31, FileTypeEnum.image),
        ]

        db = FakeDB(job, images)

        mock_clip_instance = MagicMock()
        mock_clip_instance.ensure_loaded.return_value = True
        mock_clip_cls = MagicMock(return_value=mock_clip_instance)

        with patch("app.database.SessionLocal", return_value=db), \
             patch("app.config.settings") as mock_settings, \
             patch.dict("sys.modules", {
                 "app.services.clip_classifier": MagicMock(CLIPClassifier=mock_clip_cls),
             }), \
             patch("app.services.content_classifier.classify_media") as mock_classify:
            mock_settings.CONTENT_CLASSIFICATION_METHOD = "clip"
            mock_settings.CONTENT_CLASSIFICATION_BATCH_MAX_ITEMS = 1000

            mock_classify.return_value = {
                "content_class": "anime",
                "confidence": 0.95,
            }

            from app.services.classification_job_service import run_classification_job
            run_classification_job(45)

        # Job should complete (not fail at pre-check)
        assert job.status == "completed"
        assert job.processed == 2
        assert job.classified_anime == 2


class TestRequiresClipInference:
    """Unit tests for content_classifier.requires_clip_inference helper."""

    def test_video_does_not_require_clip(self):
        from app.enums import FileTypeEnum
        from app.services.content_classifier import requires_clip_inference

        media = FakeMedia(1, FileTypeEnum.video)
        assert requires_clip_inference(media) is False

    def test_image_requires_clip(self):
        from app.enums import FileTypeEnum
        from app.services.content_classifier import requires_clip_inference

        media = FakeMedia(2, FileTypeEnum.image)
        assert requires_clip_inference(media) is True

    def test_gif_requires_clip(self):
        from app.enums import FileTypeEnum
        from app.services.content_classifier import requires_clip_inference

        media = FakeMedia(3, FileTypeEnum.gif)
        assert requires_clip_inference(media) is True
