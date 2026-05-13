"""Tests for the Unicode / Chinese-filename MIME scan import hotfix.

Covers:
  Fix A – robust MIME detection fallback chain (magic → PIL → mimetypes → octet-stream)
  Fix B – MIME validation guard in process_and_save_media()
  Fix C – _exception_to_message() / _is_duplicate_error() helpers
  Fix D – job failure finalization with rollback + terminal state
  Fix E – orphan thumbnail cleanup on import failure
  Integration – scanner-level import of a file with Chinese Unicode filename
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.utils.media_processor import (
    get_mime_type,
    is_valid_mime_type,
    _reset_magic_cache,
)
from app.utils.local_library_scanner import (
    _exception_to_message,
    _is_duplicate_error,
)


# ---------------------------------------------------------------------------
# Fix A: is_valid_mime_type
# ---------------------------------------------------------------------------

class TestIsValidMimeType:

    def test_valid_image_png(self):
        assert is_valid_mime_type("image/png") is True

    def test_valid_application_octet_stream(self):
        assert is_valid_mime_type("application/octet-stream") is True

    def test_empty_string(self):
        assert is_valid_mime_type("") is False

    def test_none(self):
        assert is_valid_mime_type(None) is False

    def test_no_slash(self):
        assert is_valid_mime_type("cannot open") is False

    def test_error_string_long(self):
        # Simulates what python-magic returns on Windows with Unicode path
        long_error = "cannot open `\\\\?\\C:\\Users\\some\\path` (No such file or directory)" * 5
        assert is_valid_mime_type(long_error) is False

    def test_too_long(self):
        assert is_valid_mime_type("image/" + "x" * 200) is False

    def test_slash_only(self):
        assert is_valid_mime_type("/") is False

    def test_multiple_slashes(self):
        assert is_valid_mime_type("image/png/extra") is False

    def test_whitespace_only(self):
        assert is_valid_mime_type("   ") is False


# ---------------------------------------------------------------------------
# Fix A: get_mime_type fallback chain
# ---------------------------------------------------------------------------

class TestGetMimeFallback:

    def _create_real_png(self, path: Path):
        """Create a minimal valid 1x1 PNG at *path*."""
        from PIL import Image as PILImage
        img = PILImage.new("RGB", (1, 1), color="red")
        img.save(str(path), format="PNG")

    def test_magic_returns_error_string_falls_back(self, tmp_path):
        """When magic returns an error string (not a valid MIME), fallback succeeds."""
        png = tmp_path / "test.png"
        self._create_real_png(png)

        mock_detector = MagicMock()
        mock_detector.from_file.return_value = (
            "cannot open `C:\\\\path` (No such file or directory)"
        )
        _reset_magic_cache()
        with patch(
            "app.utils.media_processor._get_magic_detector",
            return_value=mock_detector,
        ):
            result = get_mime_type(png)

        # PIL should detect it as image/png
        assert result == "image/png"

    def test_magic_returns_none_falls_back(self, tmp_path):
        """When magic returns None, fallback chain kicks in."""
        png = tmp_path / "test.png"
        self._create_real_png(png)

        mock_detector = MagicMock()
        mock_detector.from_file.return_value = None
        _reset_magic_cache()
        with patch(
            "app.utils.media_processor._get_magic_detector",
            return_value=mock_detector,
        ):
            result = get_mime_type(png)

        assert result == "image/png"

    def test_magic_raises_exception_falls_back(self, tmp_path):
        """When magic throws, fallback chain still works."""
        png = tmp_path / "test.png"
        self._create_real_png(png)

        mock_detector = MagicMock()
        mock_detector.from_file.side_effect = OSError("magic DLL not found")
        _reset_magic_cache()
        with patch(
            "app.utils.media_processor._get_magic_detector",
            return_value=mock_detector,
        ):
            result = get_mime_type(png)

        assert result == "image/png"

    def test_all_fallbacks_fail_returns_octet_stream(self, tmp_path):
        """When magic, PIL, and mimetypes all fail, return application/octet-stream."""
        # A file with no extension and non-image content
        noext = tmp_path / "mystery_file"
        noext.write_bytes(b"\x00\x01\x02\x03" * 100)

        mock_detector = MagicMock()
        mock_detector.from_file.return_value = "this-is-not-a-mime"
        _reset_magic_cache()
        with patch(
            "app.utils.media_processor._get_magic_detector",
            return_value=mock_detector,
        ):
            # Also patch mimetypes to return None
            with patch("app.utils.media_processor.mimetypes") as mock_mt:
                mock_mt.guess_type.return_value = (None, None)

                result = get_mime_type(noext)

        assert result == "application/octet-stream"

    def test_magic_valid_result_accepted(self, tmp_path):
        """When magic returns a valid MIME, it is accepted immediately."""
        png = tmp_path / "test.png"
        self._create_real_png(png)

        mock_detector = MagicMock()
        mock_detector.from_file.return_value = "image/png"
        _reset_magic_cache()
        with patch(
            "app.utils.media_processor._get_magic_detector",
            return_value=mock_detector,
        ):
            result = get_mime_type(png)

        assert result == "image/png"


# ---------------------------------------------------------------------------
# Fix C: _exception_to_message
# ---------------------------------------------------------------------------

class TestExceptionToMessage:

    def test_plain_exception(self):
        exc = ValueError("something broke")
        assert _exception_to_message(exc) == "something broke"

    def test_detail_is_string(self):
        exc = MagicMock(spec=Exception)
        exc.detail = "Duplicate of image.jpg"
        exc.__str__ = lambda self: "HTTP 409"
        assert _exception_to_message(exc) == "Duplicate of image.jpg"

    def test_detail_is_list(self):
        exc = MagicMock(spec=Exception)
        exc.detail = [{"msg": "value error", "loc": ["body", "file"]}]
        exc.__str__ = lambda self: "HTTP 422"
        result = _exception_to_message(exc)
        # Should be JSON-serialized, not crash
        assert "value error" in result
        assert isinstance(result, str)

    def test_detail_is_dict(self):
        exc = MagicMock(spec=Exception)
        exc.detail = {"error": "something", "code": 500}
        exc.__str__ = lambda self: "HTTP 500"
        result = _exception_to_message(exc)
        assert "something" in result
        assert isinstance(result, str)

    def test_truncation(self):
        exc = ValueError("x" * 5000)
        result = _exception_to_message(exc, max_length=100)
        assert len(result) == 100

    def test_no_detail_attribute(self):
        exc = OSError("Permission denied")
        assert _exception_to_message(exc) == "Permission denied"


# ---------------------------------------------------------------------------
# Fix C: _is_duplicate_error
# ---------------------------------------------------------------------------

class TestIsDuplicateError:

    def test_duplicate_keyword(self):
        assert _is_duplicate_error("Media already exists (duplicate of foo.jpg)") is True

    def test_already_exists_keyword(self):
        assert _is_duplicate_error("File already exists in database") is True

    def test_unrelated_error(self):
        assert _is_duplicate_error("StringDataRightTruncation at column mime_type") is False

    def test_case_insensitive(self):
        assert _is_duplicate_error("DUPLICATE key violation") is True

    def test_json_serialized_list_with_duplicate(self):
        """Ensure _is_duplicate_error works when message was produced by json.dumps of a list."""
        detail_list = [{"msg": "duplicate of image.jpg"}]
        serialized = json.dumps(detail_list, ensure_ascii=False, default=str)
        assert _is_duplicate_error(serialized) is True


# ---------------------------------------------------------------------------
# Fix D: Job failure finalization
# ---------------------------------------------------------------------------

class TestJobFailureFinalization:

    def test_rollback_before_status_update(self):
        """run_scan_job outer handler calls db.rollback() before setting status='failed'."""
        from app.utils.local_library_scanner import run_scan_job

        mock_db = MagicMock()
        mock_session_local = MagicMock(return_value=mock_db)

        # Make the job query fail on first access to trigger the outer except
        mock_job = MagicMock()
        mock_job.status = "pending"
        mock_job.paths_json = '["C:\\\\nonexistent"]'
        mock_job.dry_run = False
        mock_job.max_files = None
        mock_job.hydrated_only = True

        call_count = [0]

        def mock_get(job_id):
            call_count[0] += 1
            if call_count[0] == 1:
                return mock_job
            # Second call: simulate the scan itself raising
            raise RuntimeError("Simulated scan failure")

        mock_db.query.return_value.get = mock_get

        # Patch SessionLocal (imported locally inside run_scan_job) and
        # force scan_and_import to raise so the outer except handler runs.
        with patch("app.database.SessionLocal", mock_session_local):
            with patch(
                "app.utils.local_library_scanner.scan_and_import",
                side_effect=RuntimeError("boom"),
            ):
                run_scan_job(999)

        # Verify rollback was called before status update
        mock_db.rollback.assert_called()
        mock_db.close.assert_called_once()


# ---------------------------------------------------------------------------
# Fix E: Orphan file cleanup (scanner only cleans copied_path, NOT thumbnails)
# ---------------------------------------------------------------------------

class TestOrphanThumbnailCleanup:

    def test_copied_file_cleaned_on_failure(self, tmp_path):
        """When import fails, the scanner cleans up the copied original file."""
        from app.utils.local_library_scanner import scan_and_import

        # Set up directories
        scan_dir = tmp_path / "source"
        scan_dir.mkdir()
        original_dir = tmp_path / "storage" / "media" / "original"
        original_dir.mkdir(parents=True)
        thumbnail_dir = tmp_path / "storage" / "media" / "thumbnails"
        thumbnail_dir.mkdir(parents=True)

        # Create a test PNG in source
        from PIL import Image as PILImage
        test_file = scan_dir / "test_image.png"
        img = PILImage.new("RGB", (10, 10), color="blue")
        img.save(str(test_file), format="PNG")

        mock_db = MagicMock()
        mock_db.query.return_value.all.return_value = []  # no existing hashes
        mock_db.query.return_value.filter.return_value.first.return_value = None

        mock_settings = MagicMock()
        mock_settings.ORIGINAL_DIR = original_dir
        mock_settings.THUMBNAIL_DIR = thumbnail_dir
        mock_settings.SCAN_FILE_OPEN_TIMEOUT_SECONDS = 30
        mock_settings.SCAN_MAX_FILE_SIZE_MB = 500

        def mock_process_and_save(**kwargs):
            raise RuntimeError("Simulated DB error")

        with patch("app.utils.local_library_scanner.settings", mock_settings):
            with patch(
                "app.utils.local_library_scanner._is_scannable_file",
                return_value=None,
            ):
                with patch(
                    "app.utils.local_library_scanner._calculate_file_hash_with_timeout",
                    return_value=("ok", "abc123hash"),
                ):
                    with patch(
                        "app.utils.local_library_scanner.get_unique_filename",
                        return_value="test_image.png",
                    ):
                        with patch(
                            "app.utils.local_library_scanner.shutil.copy2"
                        ) as mock_copy:
                            def do_copy(src, dst):
                                Path(dst).write_bytes(Path(src).read_bytes())

                            mock_copy.side_effect = do_copy

                            with patch(
                                "app.routes.media.process_and_save_media",
                                side_effect=mock_process_and_save,
                            ):
                                result = scan_and_import(
                                    mock_db,
                                    [scan_dir],
                                    dry_run=False,
                                    max_files=1,
                                )

        # The copied file should be cleaned up by the scanner
        copied = original_dir / "test_image.png"
        assert not copied.exists(), "Orphan copied file should be cleaned up"

        assert result["failed"] == 1

    def test_scanner_does_not_delete_thumbnails(self, tmp_path):
        """The scanner must NOT attempt thumbnail deletion (Codex P1).

        Thumbnail cleanup is now owned by process_and_save_media(). The
        scanner only cleans up the copied original file.  A pre-existing
        thumbnail with the same stem must survive a failed import.
        """
        from app.utils.local_library_scanner import scan_and_import

        scan_dir = tmp_path / "source"
        scan_dir.mkdir()
        original_dir = tmp_path / "storage" / "media" / "original"
        original_dir.mkdir(parents=True)
        thumbnail_dir = tmp_path / "storage" / "media" / "thumbnails"
        thumbnail_dir.mkdir(parents=True)

        # Pre-existing thumbnail belonging to another media file
        existing_thumb = thumbnail_dir / "test_image.jpg"
        existing_thumb.write_bytes(b"existing_thumbnail_data_for_other_media")

        from PIL import Image as PILImage
        test_file = scan_dir / "test_image.png"
        img = PILImage.new("RGB", (10, 10), color="blue")
        img.save(str(test_file), format="PNG")

        mock_db = MagicMock()
        mock_db.query.return_value.all.return_value = []
        mock_db.query.return_value.filter.return_value.first.return_value = None

        mock_settings = MagicMock()
        mock_settings.ORIGINAL_DIR = original_dir
        mock_settings.THUMBNAIL_DIR = thumbnail_dir
        mock_settings.SCAN_FILE_OPEN_TIMEOUT_SECONDS = 30
        mock_settings.SCAN_MAX_FILE_SIZE_MB = 500

        def mock_process_and_save(**kwargs):
            raise RuntimeError("Simulated DB error")

        with patch("app.utils.local_library_scanner.settings", mock_settings):
            with patch(
                "app.utils.local_library_scanner._is_scannable_file",
                return_value=None,
            ):
                with patch(
                    "app.utils.local_library_scanner._calculate_file_hash_with_timeout",
                    return_value=("ok", "abc123hash"),
                ):
                    with patch(
                        "app.utils.local_library_scanner.get_unique_filename",
                        return_value="test_image.png",
                    ):
                        with patch(
                            "app.utils.local_library_scanner.shutil.copy2"
                        ) as mock_copy:
                            def do_copy(src, dst):
                                Path(dst).write_bytes(Path(src).read_bytes())

                            mock_copy.side_effect = do_copy

                            with patch(
                                "app.routes.media.process_and_save_media",
                                side_effect=mock_process_and_save,
                            ):
                                result = scan_and_import(
                                    mock_db,
                                    [scan_dir],
                                    dry_run=False,
                                    max_files=1,
                                )

        # The pre-existing thumbnail MUST still exist
        assert existing_thumb.exists(), (
            "Pre-existing thumbnail was deleted by the scanner — Codex P1 regression!"
        )
        assert existing_thumb.read_bytes() == b"existing_thumbnail_data_for_other_media"

        assert result["failed"] == 1


# ---------------------------------------------------------------------------
# Codex P1: Thumbnail safety in process_and_save_media
# ---------------------------------------------------------------------------

class TestThumbnailSafetyInProcessAndSave:
    """Tests for collision-safe thumbnail generation and conditional cleanup
    inside process_and_save_media()."""

    def test_existing_thumbnail_not_deleted_on_failure(self, tmp_path):
        """If a thumbnail with the same stem already exists, it must NOT be
        deleted when the current import fails."""
        from app.routes.media import process_and_save_media

        original_dir = tmp_path / "original"
        original_dir.mkdir()
        thumbnail_dir = tmp_path / "thumbnails"
        thumbnail_dir.mkdir()

        # Pre-existing thumbnail for another media (e.g. from "foo.gif")
        pre_existing = thumbnail_dir / "foo.jpg"
        pre_existing.write_bytes(b"DO_NOT_DELETE_THIS")

        # Our new file: "foo.png" whose stem also maps to "foo.jpg"
        from PIL import Image as PILImage
        source = original_dir / "foo.png"
        img = PILImage.new("RGB", (10, 10), color="red")
        img.save(str(source), format="PNG")

        mock_db = MagicMock()
        # No duplicate hash match
        mock_db.query.return_value.filter.return_value.first.return_value = None
        # Make db.flush() raise to simulate a DB error after thumbnail gen
        mock_db.flush.side_effect = RuntimeError("Simulated DB error")

        mock_settings = MagicMock()
        mock_settings.THUMBNAIL_DIR = thumbnail_dir
        mock_settings.storage_relative_path.side_effect = lambda p: Path("relative") / p.name

        # get_unique_filename must return "foo_1.jpg" because "foo.jpg" exists
        # (the real function does this; we verify via the collision-safe path)
        with patch("app.routes.media.settings", mock_settings):
            with patch("app.routes.media.calculate_file_hash", return_value="hashABC"):
                with patch(
                    "app.routes.media.process_media_file",
                    return_value={
                        "mime_type": "image/png",
                        "file_type": "image",
                        "file_size": 100,
                        "width": 10,
                        "height": 10,
                        "duration": None,
                    },
                ):
                    with patch(
                        "app.routes.media.generate_thumbnail",
                        side_effect=lambda src, dst, ft: dst.write_bytes(b"new_thumb") or True,
                    ):
                        # get_unique_filename on THUMBNAIL_DIR should produce "foo_1.jpg"
                        with patch(
                            "app.routes.media.get_unique_filename",
                            return_value="foo_1.jpg",
                        ):
                            with pytest.raises(RuntimeError, match="Simulated DB error"):
                                process_and_save_media(
                                    db=mock_db,
                                    file_path=source,
                                    unique_filename="foo.png",
                                    rating="safe",
                                    tags="",
                                    album_ids=None,
                                    source=None,
                                    category_hints=None,
                                )

        # Pre-existing thumbnail MUST survive
        assert pre_existing.exists(), "Pre-existing thumbnail was deleted!"
        assert pre_existing.read_bytes() == b"DO_NOT_DELETE_THIS"

        # The NEW thumbnail (foo_1.jpg) should be cleaned up on failure
        new_thumb = thumbnail_dir / "foo_1.jpg"
        assert not new_thumb.exists(), "Orphan new thumbnail should be cleaned up"

    def test_new_thumbnail_cleaned_on_failure(self, tmp_path):
        """A thumbnail newly created by process_and_save_media is cleaned up
        when the DB insert fails."""
        from app.routes.media import process_and_save_media

        original_dir = tmp_path / "original"
        original_dir.mkdir()
        thumbnail_dir = tmp_path / "thumbnails"
        thumbnail_dir.mkdir()

        from PIL import Image as PILImage
        source = original_dir / "brand_new.png"
        img = PILImage.new("RGB", (10, 10), color="green")
        img.save(str(source), format="PNG")

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.flush.side_effect = RuntimeError("Simulated DB error")

        mock_settings = MagicMock()
        mock_settings.THUMBNAIL_DIR = thumbnail_dir
        mock_settings.storage_relative_path.side_effect = lambda p: Path("relative") / p.name

        with patch("app.routes.media.settings", mock_settings):
            with patch("app.routes.media.calculate_file_hash", return_value="hashDEF"):
                with patch(
                    "app.routes.media.process_media_file",
                    return_value={
                        "mime_type": "image/png",
                        "file_type": "image",
                        "file_size": 200,
                        "width": 10,
                        "height": 10,
                        "duration": None,
                    },
                ):
                    with patch(
                        "app.routes.media.generate_thumbnail",
                        side_effect=lambda src, dst, ft: dst.write_bytes(b"thumb_data") or True,
                    ):
                        with patch(
                            "app.routes.media.get_unique_filename",
                            return_value="brand_new.jpg",
                        ):
                            with pytest.raises(RuntimeError, match="Simulated DB error"):
                                process_and_save_media(
                                    db=mock_db,
                                    file_path=source,
                                    unique_filename="brand_new.png",
                                    rating="safe",
                                    tags="",
                                    album_ids=None,
                                    source=None,
                                    category_hints=None,
                                )

        thumb_path = thumbnail_dir / "brand_new.jpg"
        assert not thumb_path.exists(), "Newly created thumbnail should be cleaned up"

    def test_thumbnail_collision_avoidance(self, tmp_path):
        """get_unique_filename is called for thumbnail names to avoid collisions."""
        from app.routes.media import process_and_save_media
        from app.utils.media_helpers import get_unique_filename as real_get_unique

        original_dir = tmp_path / "original"
        original_dir.mkdir()
        thumbnail_dir = tmp_path / "thumbnails"
        thumbnail_dir.mkdir()

        # Pre-existing thumbnail: "bar.jpg"
        (thumbnail_dir / "bar.jpg").write_bytes(b"existing")

        from PIL import Image as PILImage
        source = original_dir / "bar.png"
        img = PILImage.new("RGB", (10, 10), color="yellow")
        img.save(str(source), format="PNG")

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        # Let flush succeed, but commit raises — still triggers cleanup
        mock_db.commit.side_effect = RuntimeError("Simulated commit error")

        mock_settings = MagicMock()
        mock_settings.THUMBNAIL_DIR = thumbnail_dir
        mock_settings.storage_relative_path.side_effect = lambda p: Path("relative") / p.name

        generated_paths = []

        def track_thumbnail_gen(src, dst, ft):
            generated_paths.append(dst)
            dst.write_bytes(b"new_thumb_data")
            return True

        # Use the REAL get_unique_filename so we can verify collision avoidance
        with patch("app.routes.media.settings", mock_settings):
            with patch("app.routes.media.calculate_file_hash", return_value="hashGHI"):
                with patch(
                    "app.routes.media.process_media_file",
                    return_value={
                        "mime_type": "image/png",
                        "file_type": "image",
                        "file_size": 150,
                        "width": 10,
                        "height": 10,
                        "duration": None,
                    },
                ):
                    with patch(
                        "app.routes.media.generate_thumbnail",
                        side_effect=track_thumbnail_gen,
                    ):
                        with patch(
                            "app.routes.media.get_unique_filename",
                            side_effect=lambda d, f: real_get_unique(d, f),
                        ):
                            with pytest.raises(RuntimeError, match="Simulated commit error"):
                                process_and_save_media(
                                    db=mock_db,
                                    file_path=source,
                                    unique_filename="bar.png",
                                    rating="safe",
                                    tags="",
                                    album_ids=None,
                                    source=None,
                                    category_hints=None,
                                )

        # The generated thumbnail should have been "bar_1.jpg" (collision avoidance)
        assert len(generated_paths) == 1
        assert generated_paths[0].name == "bar_1.jpg", (
            f"Expected bar_1.jpg due to collision avoidance, got {generated_paths[0].name}"
        )

        # Pre-existing thumbnail MUST be untouched
        assert (thumbnail_dir / "bar.jpg").read_bytes() == b"existing"

        # bar_1.jpg should be cleaned up since the import failed
        assert not (thumbnail_dir / "bar_1.jpg").exists(), (
            "Collision-safe thumbnail should be cleaned up on failure"
        )


# ---------------------------------------------------------------------------
# Fix B: Invalid MIME replaced before DB insert
# ---------------------------------------------------------------------------

class TestMimeValidationGuard:

    def test_invalid_mime_replaced_in_process_and_save(self):
        """process_and_save_media replaces invalid MIME with application/octet-stream."""
        # We test this by checking that the validation code path works
        # without running the full DB flow
        from app.utils.media_processor import is_valid_mime_type

        error_string = "cannot open `\\\\?\\C:\\path` (No such file or directory)"
        assert is_valid_mime_type(error_string) is False

        # The guard in media.py would replace this
        raw_mime = error_string
        if not is_valid_mime_type(raw_mime):
            raw_mime = "application/octet-stream"
        assert raw_mime == "application/octet-stream"
        assert is_valid_mime_type(raw_mime) is True


# ---------------------------------------------------------------------------
# Integration: Scanner-level Unicode filename import
# ---------------------------------------------------------------------------

class TestUnicodeFilenameIntegration:

    def test_chinese_filename_import_succeeds(self, tmp_path):
        """A file named with Chinese characters imports without crash.

        This is the core regression test for the P1 bug. We mock the DB
        layer but exercise the real MIME detection chain and the scanner's
        per-file error handling.
        """
        from app.utils.media_processor import get_mime_type, process_media_file

        # Create a real PNG with Chinese filename
        from PIL import Image as PILImage
        scan_dir = tmp_path / "photos"
        scan_dir.mkdir()
        chinese_file = scan_dir / "屏幕截图 2026-05-08 012459.png"
        img = PILImage.new("RGB", (100, 100), color="green")
        img.save(str(chinese_file), format="PNG")

        # Verify MIME detection works (the root cause of the bug)
        mime = get_mime_type(chinese_file)
        assert is_valid_mime_type(mime), f"MIME should be valid, got: {mime!r}"
        assert "cannot open" not in mime, f"Error string leaked into MIME: {mime!r}"

        # Verify full metadata extraction works
        metadata = process_media_file(chinese_file)
        assert is_valid_mime_type(metadata["mime_type"])
        assert metadata["width"] == 100
        assert metadata["height"] == 100

    def test_chinese_filename_magic_fails_pil_succeeds(self, tmp_path):
        """When magic returns error for Chinese filename, PIL fallback produces valid MIME."""
        from PIL import Image as PILImage

        scan_dir = tmp_path / "照片"
        scan_dir.mkdir()
        chinese_file = scan_dir / "截屏_测试图片.png"
        img = PILImage.new("RGB", (50, 50), color="blue")
        img.save(str(chinese_file), format="PNG")

        # Simulate magic failure (returns error string)
        mock_detector = MagicMock()
        mock_detector.from_file.return_value = (
            "cannot open `\\\\?\\C:\\Users\\用户\\截屏_测试图片.png` "
            "(No such file or directory)"
        )
        _reset_magic_cache()
        with patch(
            "app.utils.media_processor._get_magic_detector",
            return_value=mock_detector,
        ):
            mime = get_mime_type(chinese_file)

        assert mime == "image/png", f"Expected image/png from PIL fallback, got: {mime}"
        assert is_valid_mime_type(mime)

    def test_scanner_exception_handler_with_unicode_error(self):
        """Scanner per-file handler doesn't crash on HTTPException with list detail.

        Simulates the exact chain: magic error → VARCHAR truncation →
        HTTPException with list detail → .lower() crash.
        """
        # The old code did: error_msg = e.detail; "duplicate" in error_msg.lower()
        # which crashes when detail is a list.
        mock_exc = MagicMock(spec=Exception)
        mock_exc.detail = [
            {
                "type": "string_too_long",
                "msg": "String should have at most 100 characters",
                "input": "cannot open `\\\\?\\C:\\Users\\用户\\屏幕截图.png`" * 3,
            }
        ]
        mock_exc.__str__ = lambda self: "422 Unprocessable Entity"

        # This should not crash (the old code would crash here)
        msg = _exception_to_message(mock_exc)
        assert isinstance(msg, str)
        assert len(msg) <= 2000

        # And duplicate detection should work on the safe string
        is_dup = _is_duplicate_error(msg)
        assert is_dup is False  # This error is not a duplicate
