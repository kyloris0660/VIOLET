"""Tests for LLM translation provider fallback classification logic."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from backend.app.services.llm_translation_provider import (
    FallbackProvider,
    LLMChunkAggregateError,
    OpenAICompatibleProvider,
    TranslationResult,
    _is_transport_error,
    _sanitize_error_message,
)


def _make_provider(label="primary", available=True):
    p = OpenAICompatibleProvider(
        api_key="sk-test" if available else "",
        model="test-model",
        base_url="https://api.example.com/v1",
        label=label,
    )
    return p


def _make_transport_error(name="ConnectError"):
    """Create an exception whose class name matches a transport error."""
    cls = type(name, (Exception,), {})
    return cls(f"simulated {name}")


def _make_http_status_error(status_code: int, message: str = ""):
    """Simulate an httpx.HTTPStatusError-like exception."""
    cls = type("HTTPStatusError", (Exception,), {})
    err = cls(message or f"HTTP {status_code}")
    err.response = type("Response", (), {"status_code": status_code})()
    return err


class TestIsTransportError:
    def test_connect_error_is_transport(self):
        assert _is_transport_error(_make_transport_error("ConnectError"))

    def test_connect_timeout_is_transport(self):
        assert _is_transport_error(_make_transport_error("ConnectTimeout"))

    def test_read_timeout_is_transport(self):
        assert _is_transport_error(_make_transport_error("ReadTimeout"))

    def test_pool_timeout_is_transport(self):
        assert _is_transport_error(_make_transport_error("PoolTimeout"))

    def test_http_status_error_is_not_transport(self):
        assert not _is_transport_error(_make_http_status_error(401))

    def test_runtime_error_is_not_transport(self):
        assert not _is_transport_error(RuntimeError("something"))

    def test_value_error_is_not_transport(self):
        assert not _is_transport_error(ValueError("bad json"))


class TestLLMChunkAggregateError:
    def test_all_transport_errors(self):
        failures = [
            _make_transport_error("ConnectError"),
            _make_transport_error("ReadTimeout"),
        ]
        err = LLMChunkAggregateError(failures, provider_label="test")
        assert err.all_transport_errors is True
        assert err.has_non_transport_errors is False

    def test_mixed_errors(self):
        failures = [
            _make_transport_error("ConnectError"),
            _make_http_status_error(401, "Unauthorized"),
        ]
        err = LLMChunkAggregateError(failures, provider_label="test")
        assert err.all_transport_errors is False
        assert err.has_non_transport_errors is True

    def test_all_non_transport_errors(self):
        failures = [
            _make_http_status_error(401),
            ValueError("bad json"),
        ]
        err = LLMChunkAggregateError(failures, provider_label="test")
        assert err.all_transport_errors is False
        assert err.has_non_transport_errors is True

    def test_sanitizes_api_keys(self):
        failures = [RuntimeError("key sk-abc123defghijk leaked")]
        err = LLMChunkAggregateError(failures, provider_label="test")
        assert "sk-abc123defghijk" not in str(err)
        assert "sk-***" in str(err)


class TestSanitizeErrorMessage:
    def test_removes_sk_key(self):
        assert "sk-abc123defg" not in _sanitize_error_message("token sk-abc123defg is invalid")
        assert "sk-***" in _sanitize_error_message("token sk-abc123defg is invalid")

    def test_removes_key_prefix(self):
        assert "key-***" in _sanitize_error_message("auth key-longapitoken12345 failed")

    def test_leaves_short_strings(self):
        result = _sanitize_error_message("connection refused")
        assert result == "connection refused"


class TestFallbackProviderClassification:
    """Tests that FallbackProvider only falls back on true transport errors."""

    def test_transport_error_triggers_fallback(self):
        async def _run():
            primary = _make_provider("primary")
            fallback = _make_provider("fallback")
            fb = FallbackProvider(primary, fallback)

            expected = [TranslationResult("tag", "标签")]
            primary.translate_tags = AsyncMock(side_effect=_make_transport_error("ConnectError"))
            fallback.translate_tags = AsyncMock(return_value=expected)

            result = await fb.translate_tags([{"name": "tag", "category": "general"}])
            assert result == expected
            fallback.translate_tags.assert_called_once()
        asyncio.run(_run())

    def test_aggregate_all_transport_triggers_fallback(self):
        async def _run():
            primary = _make_provider("primary")
            fallback = _make_provider("fallback")
            fb = FallbackProvider(primary, fallback)

            agg_err = LLMChunkAggregateError(
                [_make_transport_error("ConnectError"), _make_transport_error("ReadTimeout")],
                provider_label="primary",
            )
            expected = [TranslationResult("tag", "标签")]
            primary.translate_tags = AsyncMock(side_effect=agg_err)
            fallback.translate_tags = AsyncMock(return_value=expected)

            result = await fb.translate_tags([{"name": "tag", "category": "general"}])
            assert result == expected
            fallback.translate_tags.assert_called_once()
        asyncio.run(_run())

    def test_http_401_does_not_trigger_fallback(self):
        async def _run():
            primary = _make_provider("primary")
            fallback = _make_provider("fallback")
            fb = FallbackProvider(primary, fallback)

            primary.translate_tags = AsyncMock(side_effect=_make_http_status_error(401, "Unauthorized"))
            fallback.translate_tags = AsyncMock()

            with pytest.raises(Exception, match="Unauthorized"):
                await fb.translate_tags([{"name": "tag", "category": "general"}])
            fallback.translate_tags.assert_not_called()
        asyncio.run(_run())

    def test_http_400_does_not_trigger_fallback(self):
        async def _run():
            primary = _make_provider("primary")
            fallback = _make_provider("fallback")
            fb = FallbackProvider(primary, fallback)

            primary.translate_tags = AsyncMock(side_effect=_make_http_status_error(400, "Bad Request"))
            fallback.translate_tags = AsyncMock()

            with pytest.raises(Exception, match="Bad Request"):
                await fb.translate_tags([{"name": "tag", "category": "general"}])
            fallback.translate_tags.assert_not_called()
        asyncio.run(_run())

    def test_json_parse_error_does_not_trigger_fallback(self):
        async def _run():
            primary = _make_provider("primary")
            fallback = _make_provider("fallback")
            fb = FallbackProvider(primary, fallback)

            primary.translate_tags = AsyncMock(side_effect=ValueError("Invalid JSON response"))
            fallback.translate_tags = AsyncMock()

            with pytest.raises(ValueError, match="Invalid JSON"):
                await fb.translate_tags([{"name": "tag", "category": "general"}])
            fallback.translate_tags.assert_not_called()
        asyncio.run(_run())

    def test_aggregate_mixed_errors_does_not_trigger_fallback(self):
        """If aggregate contains ANY non-transport error, don't fallback."""
        async def _run():
            primary = _make_provider("primary")
            fallback = _make_provider("fallback")
            fb = FallbackProvider(primary, fallback)

            agg_err = LLMChunkAggregateError(
                [_make_transport_error("ConnectError"), _make_http_status_error(403, "Forbidden")],
                provider_label="primary",
            )
            primary.translate_tags = AsyncMock(side_effect=agg_err)
            fallback.translate_tags = AsyncMock()

            with pytest.raises(LLMChunkAggregateError):
                await fb.translate_tags([{"name": "tag", "category": "general"}])
            fallback.translate_tags.assert_not_called()
        asyncio.run(_run())

    def test_error_messages_do_not_expose_api_keys(self):
        async def _run():
            primary = _make_provider("primary")
            fallback = _make_provider("fallback")
            fb = FallbackProvider(primary, fallback)

            failures = [RuntimeError("auth failed for sk-realkey1234567890")]
            agg_err = LLMChunkAggregateError(failures, provider_label="primary")
            primary.translate_tags = AsyncMock(side_effect=agg_err)
            fallback.translate_tags = AsyncMock()

            with pytest.raises(LLMChunkAggregateError) as exc_info:
                await fb.translate_tags([{"name": "tag", "category": "general"}])
            assert "sk-realkey1234567890" not in str(exc_info.value)
            assert "sk-***" in str(exc_info.value)
        asyncio.run(_run())
