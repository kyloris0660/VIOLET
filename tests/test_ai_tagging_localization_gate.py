"""Regression tests for AI tagging localization side-effect gate (Phase 3.2g.1).

These tests verify:
1. _schedule_localization skips when AI_TAGGING_AUTO_LOCALIZATION=false
2. _schedule_localization proceeds when AI_TAGGING_AUTO_LOCALIZATION=true (default)
3. _schedule_localization skips on dry_run regardless of flag
4. _schedule_localization skips when no new tags exist

No real AI tagging, no real LLM, no DB mutation.
"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure backend is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))


@pytest.fixture
def mock_settings():
    """Create a mock settings object with configurable properties."""
    mock = MagicMock()
    mock.TAG_TRANSLATION_BG_ENABLED = True
    mock.TAG_TRANSLATION_LLM_ENABLED = True
    mock.TAG_TRANSLATION_AUTO_ENABLED = True
    mock.AI_TAGGING_AUTO_LOCALIZATION = True
    return mock


@pytest.fixture
def mock_job():
    """Create a mock AITagJob."""
    job = MagicMock()
    job.id = 99
    job.dry_run = False
    job.localization_status = None
    return job


class TestScheduleLocalizationGate:
    """Test the AI_TAGGING_AUTO_LOCALIZATION gate in _schedule_localization."""

    def test_gate_disabled_skips_localization(self, mock_settings, mock_job):
        """When AI_TAGGING_AUTO_LOCALIZATION=false, localization is skipped
        and localization_status is set to 'skipped_auto_localization_disabled'."""
        mock_settings.AI_TAGGING_AUTO_LOCALIZATION = False

        with patch("app.services.ai_tagging_job_service.settings", mock_settings):
            from app.services.ai_tagging_job_service import _schedule_localization
            _schedule_localization(mock_job, ["tag_a", "tag_b", "tag_c"])

        assert mock_job.localization_status == "skipped_auto_localization_disabled"

    def test_gate_enabled_proceeds_to_localization(self, mock_settings, mock_job):
        """When AI_TAGGING_AUTO_LOCALIZATION=true (default), localization
        proceeds and triggers the background worker."""
        mock_settings.AI_TAGGING_AUTO_LOCALIZATION = True

        mock_worker_thread = MagicMock()
        mock_worker_thread.is_alive.return_value = True

        with patch("app.services.ai_tagging_job_service.settings", mock_settings), \
             patch.dict("sys.modules", {
                 "app.services.tag_translation_worker": MagicMock(
                     trigger_run_now=MagicMock(),
                     _worker_thread=mock_worker_thread,
                 )
             }):
            from app.services.ai_tagging_job_service import _schedule_localization
            _schedule_localization(mock_job, ["tag_a", "tag_b"])

        # Should have queued tags — status should contain "queued" and tag count
        assert mock_job.localization_status is not None
        assert "queued" in mock_job.localization_status
        assert "2" in mock_job.localization_status  # 2 unique tags

    def test_dry_run_skips_regardless_of_gate(self, mock_settings, mock_job):
        """Dry-run jobs always skip localization, regardless of gate setting."""
        mock_job.dry_run = True
        mock_settings.AI_TAGGING_AUTO_LOCALIZATION = True

        with patch("app.services.ai_tagging_job_service.settings", mock_settings):
            from app.services.ai_tagging_job_service import _schedule_localization
            _schedule_localization(mock_job, ["tag_a"])

        assert mock_job.localization_status == "skipped_dry_run"

    def test_no_new_tags_skips_regardless_of_gate(self, mock_settings, mock_job):
        """No new tags always skip localization, regardless of gate setting."""
        mock_settings.AI_TAGGING_AUTO_LOCALIZATION = True

        with patch("app.services.ai_tagging_job_service.settings", mock_settings):
            from app.services.ai_tagging_job_service import _schedule_localization
            _schedule_localization(mock_job, [])

        assert mock_job.localization_status == "skipped_no_new_tags"


class TestAITaggingAutoLocalizationConfig:
    """Test AI_TAGGING_AUTO_LOCALIZATION config property."""

    def test_default_is_true(self, reload_settings):
        """Default value should be True (backward compatible)."""
        s = reload_settings({"AI_TAGGING_AUTO_LOCALIZATION": ""})
        # When env var is empty string, should fall back to default "true"
        # Actually empty string won't match "true", so let's test unset
        with patch.dict(os.environ, {}, clear=False):
            # Remove the key if it exists
            env_copy = os.environ.copy()
            env_copy.pop("AI_TAGGING_AUTO_LOCALIZATION", None)
            with patch.dict(os.environ, env_copy, clear=True):
                s2 = reload_settings()
                assert s2.AI_TAGGING_AUTO_LOCALIZATION is True

    def test_explicit_false(self, reload_settings):
        """Setting AI_TAGGING_AUTO_LOCALIZATION=false disables it."""
        with patch.dict(os.environ, {"AI_TAGGING_AUTO_LOCALIZATION": "false"}):
            s = reload_settings({"AI_TAGGING_AUTO_LOCALIZATION": "false"})
            assert s.AI_TAGGING_AUTO_LOCALIZATION is False

    def test_explicit_true(self, reload_settings):
        """Setting AI_TAGGING_AUTO_LOCALIZATION=true enables it."""
        with patch.dict(os.environ, {"AI_TAGGING_AUTO_LOCALIZATION": "true"}):
            s = reload_settings({"AI_TAGGING_AUTO_LOCALIZATION": "true"})
            assert s.AI_TAGGING_AUTO_LOCALIZATION is True

    def test_explicit_zero(self, reload_settings):
        """Setting AI_TAGGING_AUTO_LOCALIZATION=0 disables it."""
        with patch.dict(os.environ, {"AI_TAGGING_AUTO_LOCALIZATION": "0"}):
            s = reload_settings({"AI_TAGGING_AUTO_LOCALIZATION": "0"})
            assert s.AI_TAGGING_AUTO_LOCALIZATION is False
