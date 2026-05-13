"""Regression tests for AI tagging localization side-effect gate (Phase 3.2g.1).

These tests verify:
1. _schedule_localization skips when AI_TAGGING_AUTO_LOCALIZATION=false
2. _schedule_localization proceeds when AI_TAGGING_AUTO_LOCALIZATION=true (default)
3. _schedule_localization skips on dry_run regardless of flag
4. _schedule_localization skips when no new tags exist

No real AI tagging, no real LLM, no DB mutation.
"""
import importlib
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
    """Test AI_TAGGING_AUTO_LOCALIZATION config property parsing.

    Covers: unset, empty string, whitespace, truthy values (true/1/yes/on),
    falsy values (false/0/no/off), and case-insensitive matching.

    The property reads os.getenv at access time, so we must ensure the env
    var is patched when the property is accessed, not just at reload time.
    """

    def _get_value(self, env_val=None):
        """Build a Settings object and read the property with env patched."""
        import app.config as config_mod
        import dotenv
        env = {"VIOLET_ENV": "test", "POSTGRES_DB": "blombooru_test"}
        if env_val is not None:
            env["AI_TAGGING_AUTO_LOCALIZATION"] = env_val
        # else: key absent from env → os.getenv returns default
        with patch.dict(os.environ, env, clear=True), \
             patch.object(dotenv, "load_dotenv", lambda *a, **kw: None):
            importlib.reload(config_mod)
            s = config_mod.Settings()
            return s.AI_TAGGING_AUTO_LOCALIZATION

    # -- Unset / empty → default True --

    def test_unset_defaults_to_true(self):
        """When AI_TAGGING_AUTO_LOCALIZATION is not in env, default is True."""
        assert self._get_value() is True

    def test_empty_string_defaults_to_true(self):
        """Empty string should be treated as unset → default True."""
        assert self._get_value("") is True

    def test_whitespace_only_defaults_to_true(self):
        """Whitespace-only value should be treated as unset → default True."""
        assert self._get_value("   ") is True

    # -- Truthy values --

    @pytest.mark.parametrize("val", ["true", "True", "TRUE", "1", "yes", "Yes", "on", "On"])
    def test_truthy_values(self, val):
        """All standard truthy values → True."""
        assert self._get_value(val) is True

    # -- Falsy values --

    @pytest.mark.parametrize("val", ["false", "False", "FALSE", "0", "no", "No", "off", "Off"])
    def test_falsy_values(self, val):
        """All standard falsy values → False."""
        assert self._get_value(val) is False
