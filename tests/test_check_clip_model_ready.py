"""Tests for scripts/check_clip_model_ready.py preflight check.

Mocks CLIPClassifier to verify behavior on success, load failure,
and import error scenarios without requiring the actual CLIP model.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import the script module directly (same pattern as test_check_server_identity_script.py)
_script_path = Path(__file__).resolve().parent.parent / "scripts" / "check_clip_model_ready.py"
_spec = importlib.util.spec_from_file_location("check_clip_ready_mod", _script_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

check_clip_ready = _mod.check_clip_ready
main = _mod.main


# ---------------------------------------------------------------------------
# 1. Successful load
# ---------------------------------------------------------------------------

class TestSuccessfulLoad:
    """CLIP model loads successfully — check_clip_ready returns ready=True."""

    def test_ready_true_on_successful_load(self):
        mock_classifier = MagicMock()
        mock_classifier.ensure_loaded.return_value = True
        mock_classifier.model_info.return_value = {
            "provider": "clip_zero_shot",
            "model": "clip-vit-base-patch32",
            "loaded": True,
            "categories": ["anime_style", "non_anime"],
        }

        mock_cls = MagicMock(return_value=mock_classifier)

        with patch.dict("sys.modules", {
            "app.services.clip_classifier": MagicMock(CLIPClassifier=mock_cls),
        }):
            result = check_clip_ready(verbose=False)

        assert result["ready"] is True
        assert result["error"] is None
        assert result["model_info"] is not None
        assert result["model_info"]["provider"] == "clip_zero_shot"
        assert result["model_info"]["loaded"] is True
        assert isinstance(result["elapsed_ms"], int)
        assert result["elapsed_ms"] >= 0

    def test_ready_resets_failure_state(self):
        """The script resets _load_failed before calling ensure_loaded."""
        mock_classifier = MagicMock()
        mock_classifier._load_failed = True
        mock_classifier._load_error = "old error"
        mock_classifier._load_failed_at = 12345.0
        mock_classifier.ensure_loaded.return_value = True
        mock_classifier.model_info.return_value = {
            "provider": "clip_zero_shot",
            "model": "test",
            "loaded": True,
            "categories": [],
        }

        mock_cls = MagicMock(return_value=mock_classifier)

        with patch.dict("sys.modules", {
            "app.services.clip_classifier": MagicMock(CLIPClassifier=mock_cls),
        }):
            result = check_clip_ready(verbose=False)

        # Verify the script reset the failure state before calling ensure_loaded
        assert mock_classifier._load_failed is False
        assert mock_classifier._load_error is None
        assert mock_classifier._load_failed_at is None
        assert result["ready"] is True

    def test_verbose_output(self, capsys):
        """verbose=True prints OK message with cache-only note."""
        mock_classifier = MagicMock()
        mock_classifier.ensure_loaded.return_value = True
        mock_classifier.model_info.return_value = {
            "provider": "clip_zero_shot",
            "model": "clip-vit-base-patch32",
            "loaded": True,
            "categories": ["anime_style", "non_anime"],
        }

        mock_cls = MagicMock(return_value=mock_classifier)

        with patch.dict("sys.modules", {
            "app.services.clip_classifier": MagicMock(CLIPClassifier=mock_cls),
        }):
            check_clip_ready(verbose=True)

        captured = capsys.readouterr()
        assert "OK: CLIP model ready" in captured.out
        assert "cache-only" in captured.out
        assert "clip_zero_shot" in captured.out


# ---------------------------------------------------------------------------
# 2. Load failure (ensure_loaded returns False)
# ---------------------------------------------------------------------------

class TestLoadFailure:
    """CLIP model fails to load — check_clip_ready returns ready=False."""

    def test_ready_false_when_ensure_loaded_fails(self):
        mock_classifier = MagicMock()
        # The script resets _load_error = None before calling ensure_loaded(),
        # so we must set _load_error via side_effect (mimicking real behavior
        # where ensure_loaded() sets _load_error on failure).
        def fail_and_set_error():
            mock_classifier._load_error = "Model file not found in cache"
            return False
        mock_classifier.ensure_loaded.side_effect = fail_and_set_error

        mock_cls = MagicMock(return_value=mock_classifier)

        with patch.dict("sys.modules", {
            "app.services.clip_classifier": MagicMock(CLIPClassifier=mock_cls),
        }):
            result = check_clip_ready(verbose=False)

        assert result["ready"] is False
        assert result["error"] == "Model file not found in cache"
        assert result["model_info"] is None
        assert isinstance(result["elapsed_ms"], int)

    def test_ready_false_with_generic_error(self):
        """When _load_error is None, a generic message is used."""
        mock_classifier = MagicMock()
        mock_classifier.ensure_loaded.return_value = False
        mock_classifier._load_error = None

        mock_cls = MagicMock(return_value=mock_classifier)

        with patch.dict("sys.modules", {
            "app.services.clip_classifier": MagicMock(CLIPClassifier=mock_cls),
        }):
            result = check_clip_ready(verbose=False)

        assert result["ready"] is False
        assert result["error"] == "ensure_loaded() returned False"

    def test_verbose_failure_output(self, capsys):
        """verbose=True prints FAIL message on load failure."""
        mock_classifier = MagicMock()
        def fail_and_set_error():
            mock_classifier._load_error = "Cache miss"
            return False
        mock_classifier.ensure_loaded.side_effect = fail_and_set_error

        mock_cls = MagicMock(return_value=mock_classifier)

        with patch.dict("sys.modules", {
            "app.services.clip_classifier": MagicMock(CLIPClassifier=mock_cls),
        }):
            check_clip_ready(verbose=True)

        captured = capsys.readouterr()
        assert "FAIL" in captured.out
        assert "Cache miss" in captured.out


# ---------------------------------------------------------------------------
# 3. Import error (CLIPClassifier not importable)
# ---------------------------------------------------------------------------

class TestImportError:
    """CLIPClassifier cannot be imported — check_clip_ready returns ready=False."""

    def test_ready_false_on_import_error(self):
        """Simulate missing dependencies (onnxruntime, etc.)."""
        # Remove the mock module so the import inside check_clip_ready fails
        with patch.dict("sys.modules", {
            "app.services.clip_classifier": None,
        }):
            # Python raises ImportError when trying to import from a module
            # whose sys.modules entry is None
            result = check_clip_ready(verbose=False)

        assert result["ready"] is False
        assert "Import error" in result["error"] or "import" in result["error"].lower()
        assert result["model_info"] is None

    def test_ready_false_on_unexpected_exception(self):
        """Simulate an unexpected exception during classifier construction."""
        def raise_runtime():
            raise RuntimeError("ONNX runtime init failed")

        mock_mod = MagicMock()
        mock_mod.CLIPClassifier.side_effect = raise_runtime

        with patch.dict("sys.modules", {
            "app.services.clip_classifier": mock_mod,
        }):
            result = check_clip_ready(verbose=False)

        assert result["ready"] is False
        assert "RuntimeError" in result["error"]
        assert "ONNX runtime init failed" in result["error"]


# ---------------------------------------------------------------------------
# 4. Return dict structure
# ---------------------------------------------------------------------------

class TestReturnStructure:
    """Verify the returned dict always has the expected keys."""

    @pytest.fixture(autouse=True)
    def _mock_classifier(self):
        mock_classifier = MagicMock()
        mock_classifier.ensure_loaded.return_value = True
        mock_classifier.model_info.return_value = {
            "provider": "test",
            "model": "test",
            "loaded": True,
            "categories": [],
        }
        mock_cls = MagicMock(return_value=mock_classifier)
        with patch.dict("sys.modules", {
            "app.services.clip_classifier": MagicMock(CLIPClassifier=mock_cls),
        }):
            yield

    def test_all_keys_present_on_success(self):
        result = check_clip_ready(verbose=False)
        expected_keys = {"ready", "model_info", "error", "elapsed_ms", "hf_hub_offline", "cache_only"}
        assert set(result.keys()) == expected_keys

    def test_all_keys_present_on_failure(self):
        mock_classifier = MagicMock()
        def fail_and_set_error():
            mock_classifier._load_error = "fail"
            return False
        mock_classifier.ensure_loaded.side_effect = fail_and_set_error
        mock_cls = MagicMock(return_value=mock_classifier)

        with patch.dict("sys.modules", {
            "app.services.clip_classifier": MagicMock(CLIPClassifier=mock_cls),
        }):
            result = check_clip_ready(verbose=False)

        expected_keys = {"ready", "model_info", "error", "elapsed_ms", "hf_hub_offline", "cache_only"}
        assert set(result.keys()) == expected_keys

    def test_hf_hub_offline_reflects_forced_value(self):
        """hf_hub_offline field should always be '1' during the check (forced by script)."""
        # Even if HF_HUB_OFFLINE is unset, the script forces it to '1'
        env = os.environ.copy()
        env.pop("HF_HUB_OFFLINE", None)
        with patch.dict(os.environ, env, clear=True):
            result = check_clip_ready(verbose=False)
        assert result["hf_hub_offline"] == "1"
        assert result["cache_only"] is True

    def test_hf_hub_offline_preserves_existing_value(self):
        """When HF_HUB_OFFLINE was already set, the script should restore it after check."""
        with patch.dict(os.environ, {"HF_HUB_OFFLINE": "1"}):
            result = check_clip_ready(verbose=False)
            # During the check it was '1'
            assert result["hf_hub_offline"] == "1"
            # After check_clip_ready returns, original value should be restored
            assert os.environ.get("HF_HUB_OFFLINE") == "1"

    def test_hf_hub_offline_restored_when_unset(self):
        """When HF_HUB_OFFLINE was NOT set before, it should be removed after check."""
        env = os.environ.copy()
        env.pop("HF_HUB_OFFLINE", None)
        with patch.dict(os.environ, env, clear=True):
            check_clip_ready(verbose=False)
            # After check_clip_ready returns, env var should be gone again
            assert "HF_HUB_OFFLINE" not in os.environ

    def test_cache_only_always_true(self):
        """cache_only field should always be True."""
        result = check_clip_ready(verbose=False)
        assert result["cache_only"] is True


# ---------------------------------------------------------------------------
# 5. main() exit codes
# ---------------------------------------------------------------------------

class TestMainExitCodes:
    """main() should exit 0 on success, 1 on failure."""

    def test_exit_0_on_success(self):
        mock_classifier = MagicMock()
        mock_classifier.ensure_loaded.return_value = True
        mock_classifier.model_info.return_value = {
            "provider": "test",
            "model": "test",
            "loaded": True,
            "categories": [],
        }
        mock_cls = MagicMock(return_value=mock_classifier)

        with patch.dict("sys.modules", {
            "app.services.clip_classifier": MagicMock(CLIPClassifier=mock_cls),
        }), patch("sys.argv", ["prog"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_exit_1_on_failure(self):
        mock_classifier = MagicMock()
        def fail_and_set_error():
            mock_classifier._load_error = "not cached"
            return False
        mock_classifier.ensure_loaded.side_effect = fail_and_set_error
        mock_cls = MagicMock(return_value=mock_classifier)

        with patch.dict("sys.modules", {
            "app.services.clip_classifier": MagicMock(CLIPClassifier=mock_cls),
        }), patch("sys.argv", ["prog"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_json_output_on_success(self, capsys):
        mock_classifier = MagicMock()
        mock_classifier.ensure_loaded.return_value = True
        mock_classifier.model_info.return_value = {
            "provider": "clip_zero_shot",
            "model": "test",
            "loaded": True,
            "categories": ["a", "b"],
        }
        mock_cls = MagicMock(return_value=mock_classifier)

        with patch.dict("sys.modules", {
            "app.services.clip_classifier": MagicMock(CLIPClassifier=mock_cls),
        }), patch("sys.argv", ["prog", "--json"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["ready"] is True
        assert data["model_info"]["provider"] == "clip_zero_shot"

    def test_json_output_on_failure(self, capsys):
        mock_classifier = MagicMock()
        def fail_and_set_error():
            mock_classifier._load_error = "no cache"
            return False
        mock_classifier.ensure_loaded.side_effect = fail_and_set_error
        mock_cls = MagicMock(return_value=mock_classifier)

        with patch.dict("sys.modules", {
            "app.services.clip_classifier": MagicMock(CLIPClassifier=mock_cls),
        }), patch("sys.argv", ["prog", "--json"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["ready"] is False
        assert data["error"] == "no cache"

    def test_json_output_includes_cache_only(self, capsys):
        """JSON output should include cache_only=True."""
        mock_classifier = MagicMock()
        mock_classifier.ensure_loaded.return_value = True
        mock_classifier.model_info.return_value = {
            "provider": "test",
            "model": "test",
            "loaded": True,
            "categories": [],
        }
        mock_cls = MagicMock(return_value=mock_classifier)

        with patch.dict("sys.modules", {
            "app.services.clip_classifier": MagicMock(CLIPClassifier=mock_cls),
        }), patch("sys.argv", ["prog", "--json"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["cache_only"] is True
        assert data["hf_hub_offline"] == "1"


# ---------------------------------------------------------------------------
# 6. Forced cache-only / offline behavior
# ---------------------------------------------------------------------------

class TestForcedCacheOnly:
    """Verify the script forces HF_HUB_OFFLINE=1 before loading the model."""

    @pytest.fixture(autouse=True)
    def _mock_classifier(self):
        mock_classifier = MagicMock()
        mock_classifier.ensure_loaded.return_value = True
        mock_classifier.model_info.return_value = {
            "provider": "test",
            "model": "test",
            "loaded": True,
            "categories": [],
        }
        mock_cls = MagicMock(return_value=mock_classifier)
        with patch.dict("sys.modules", {
            "app.services.clip_classifier": MagicMock(CLIPClassifier=mock_cls),
        }):
            yield

    def test_forces_offline_when_unset(self):
        """When HF_HUB_OFFLINE is not set, script forces it to '1' during check."""
        env = os.environ.copy()
        env.pop("HF_HUB_OFFLINE", None)
        with patch.dict(os.environ, env, clear=True):
            result = check_clip_ready(verbose=False)
            assert result["hf_hub_offline"] == "1"
            assert result["cache_only"] is True
            # After the call, the env var should be cleaned up
            assert "HF_HUB_OFFLINE" not in os.environ

    def test_preserves_offline_when_already_set(self):
        """When HF_HUB_OFFLINE is already '1', it stays '1' after check."""
        with patch.dict(os.environ, {"HF_HUB_OFFLINE": "1"}):
            result = check_clip_ready(verbose=False)
            assert result["hf_hub_offline"] == "1"
            assert os.environ["HF_HUB_OFFLINE"] == "1"

    def test_restores_previous_value_after_check(self):
        """If HF_HUB_OFFLINE was set to something else, it's restored."""
        with patch.dict(os.environ, {"HF_HUB_OFFLINE": "0"}):
            result = check_clip_ready(verbose=False)
            # During check it was forced to '1'
            assert result["hf_hub_offline"] == "1"
            # After check, restored to original '0'
            assert os.environ["HF_HUB_OFFLINE"] == "0"

    def test_failure_still_cache_only(self):
        """Even on failure, cache_only is True and env is restored."""
        mock_classifier = MagicMock()
        def fail_and_set_error():
            mock_classifier._load_error = "not in cache"
            return False
        mock_classifier.ensure_loaded.side_effect = fail_and_set_error
        mock_cls = MagicMock(return_value=mock_classifier)

        env = os.environ.copy()
        env.pop("HF_HUB_OFFLINE", None)
        with patch.dict(os.environ, env, clear=True), patch.dict("sys.modules", {
            "app.services.clip_classifier": MagicMock(CLIPClassifier=mock_cls),
        }):
            result = check_clip_ready(verbose=False)
            assert result["ready"] is False
            assert result["cache_only"] is True
            assert result["hf_hub_offline"] == "1"
            # Env restored — HF_HUB_OFFLINE removed
            assert "HF_HUB_OFFLINE" not in os.environ

    def test_import_error_still_cache_only(self):
        """Import error path also has cache_only=True and restores env."""
        env = os.environ.copy()
        env.pop("HF_HUB_OFFLINE", None)
        with patch.dict(os.environ, env, clear=True), patch.dict("sys.modules", {
            "app.services.clip_classifier": None,
        }):
            result = check_clip_ready(verbose=False)
            assert result["ready"] is False
            assert result["cache_only"] is True
            assert "HF_HUB_OFFLINE" not in os.environ
