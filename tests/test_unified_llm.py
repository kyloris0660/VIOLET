"""Unit tests for the unified LLM provider (complete_chat / complete_json).

Covers: success paths, transport fallback, HTTP 4xx vs 5xx, JSON parse errors,
error sanitization, LLMAllProvidersFailed, _should_fallback logic.
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.services.llm_translation_provider import (
    BaseLLMProvider,
    DisabledProvider,
    FallbackProvider,
    LLMAllProvidersFailed,
    LLMBatchAggregateError,
    LLMHTTPStatusError,
    LLMProviderError,
    LLMResponseFormatError,
    LLMTransportError,
    OpenAICompatibleProvider,
    _sanitize_error_message,
    _should_fallback,
)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_provider(label="primary"):
    return OpenAICompatibleProvider(
        api_key="sk-test-key", model="test-model",
        base_url="https://api.test.com/v1", label=label,
    )


def _mock_httpx_success(content="hello world"):
    """Return a mock httpx response for a successful chat completion."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    return resp


def _mock_httpx_error(status_code, body="error"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = body
    return resp


# ---------------------------------------------------------------------------
# complete_chat — basic success
# ---------------------------------------------------------------------------

class TestCompleteChatSuccess:

    def test_returns_content_string(self):
        provider = _make_provider()
        mock_resp = _mock_httpx_success("test content")

        async def mock_post(*args, **kwargs):
            return mock_resp

        with patch("httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = mock_post
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = _run(provider.complete_chat(
                [{"role": "user", "content": "hi"}]
            ))

        assert result == "test content"
        assert isinstance(result, str)

    def test_strips_whitespace(self):
        provider = _make_provider()
        mock_resp = _mock_httpx_success("  trimmed  \n")

        async def mock_post(*args, **kwargs):
            return mock_resp

        with patch("httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = mock_post
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = _run(provider.complete_chat(
                [{"role": "user", "content": "hi"}]
            ))

        assert result == "trimmed"


# ---------------------------------------------------------------------------
# complete_chat — transport errors
# ---------------------------------------------------------------------------

class TestCompleteChatTransportError:

    def test_connect_error_raises_llm_transport(self):
        provider = _make_provider()

        class FakeConnectError(Exception):
            pass

        FakeConnectError.__name__ = "ConnectError"

        async def mock_post(*args, **kwargs):
            raise FakeConnectError("Connection refused")

        with patch("httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = mock_post
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            with pytest.raises(LLMTransportError):
                _run(provider.complete_chat(
                    [{"role": "user", "content": "hi"}]
                ))

    def test_read_timeout_raises_llm_transport(self):
        provider = _make_provider()

        class FakeReadTimeout(Exception):
            pass

        FakeReadTimeout.__name__ = "ReadTimeout"

        async def mock_post(*args, **kwargs):
            raise FakeReadTimeout("Read timed out")

        with patch("httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = mock_post
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            with pytest.raises(LLMTransportError):
                _run(provider.complete_chat(
                    [{"role": "user", "content": "hi"}]
                ))


# ---------------------------------------------------------------------------
# complete_chat — HTTP status errors
# ---------------------------------------------------------------------------

class TestCompleteChatHTTPErrors:

    @pytest.mark.parametrize("code", [500, 502, 503, 504, 408, 429])
    def test_fallback_eligible_codes(self, code):
        provider = _make_provider()
        mock_resp = _mock_httpx_error(code)

        async def mock_post(*args, **kwargs):
            return mock_resp

        with patch("httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = mock_post
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            with pytest.raises(LLMHTTPStatusError) as exc_info:
                _run(provider.complete_chat(
                    [{"role": "user", "content": "hi"}]
                ))

            assert exc_info.value.status_code == code
            assert exc_info.value.should_fallback is True

    @pytest.mark.parametrize("code", [400, 401, 403, 404])
    def test_non_fallback_codes(self, code):
        provider = _make_provider()
        mock_resp = _mock_httpx_error(code)

        async def mock_post(*args, **kwargs):
            return mock_resp

        with patch("httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = mock_post
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            with pytest.raises(LLMHTTPStatusError) as exc_info:
                _run(provider.complete_chat(
                    [{"role": "user", "content": "hi"}]
                ))

            assert exc_info.value.status_code == code
            assert exc_info.value.should_fallback is False


# ---------------------------------------------------------------------------
# complete_json — success + parse errors
# ---------------------------------------------------------------------------

class TestCompleteJson:

    def test_parses_json_array(self):
        provider = _make_provider()
        provider.complete_chat = AsyncMock(
            return_value='[{"tag": "blue_eyes", "zh": "蓝眼睛"}]'
        )

        result = _run(provider.complete_json(
            [{"role": "user", "content": "translate"}]
        ))

        assert isinstance(result, list)
        assert result[0]["tag"] == "blue_eyes"

    def test_parses_json_object(self):
        provider = _make_provider()
        provider.complete_chat = AsyncMock(
            return_value='{"key": "value"}'
        )

        result = _run(provider.complete_json(
            [{"role": "user", "content": "translate"}]
        ))

        assert result["key"] == "value"

    def test_strips_markdown_fences(self):
        provider = _make_provider()
        provider.complete_chat = AsyncMock(
            return_value='```json\n{"stripped": true}\n```'
        )

        result = _run(provider.complete_json(
            [{"role": "user", "content": "translate"}]
        ))

        assert result["stripped"] is True

    def test_invalid_json_raises_format_error(self):
        provider = _make_provider()
        provider.complete_chat = AsyncMock(
            return_value="This is not JSON at all"
        )

        with pytest.raises(LLMResponseFormatError):
            _run(provider.complete_json(
                [{"role": "user", "content": "translate"}]
            ))

    def test_format_error_no_fallback(self):
        exc = LLMResponseFormatError("bad json")
        assert _should_fallback(exc) is False


# ---------------------------------------------------------------------------
# DisabledProvider
# ---------------------------------------------------------------------------

class TestDisabledProvider:

    def test_not_available(self):
        p = DisabledProvider()
        assert p.is_available() is False

    def test_complete_chat_raises(self):
        p = DisabledProvider()
        with pytest.raises(LLMProviderError, match="disabled"):
            _run(p.complete_chat([{"role": "user", "content": "hi"}]))

    def test_translate_tags_returns_empty(self):
        p = DisabledProvider()
        result = _run(p.translate_tags([{"name": "test", "category": "general"}]))
        assert result == []


# ---------------------------------------------------------------------------
# FallbackProvider — complete_chat
# ---------------------------------------------------------------------------

class TestFallbackProviderCompleteChat:

    def test_primary_success_no_fallback(self):
        primary = _make_provider("primary")
        fallback = _make_provider("fallback")
        primary.complete_chat = AsyncMock(return_value="primary result")
        fallback.complete_chat = AsyncMock(return_value="fallback result")

        fp = FallbackProvider(primary, fallback)
        result = _run(fp.complete_chat([{"role": "user", "content": "hi"}]))

        assert result == "primary result"
        fallback.complete_chat.assert_not_called()

    def test_transport_error_triggers_fallback(self):
        primary = _make_provider("primary")
        fallback = _make_provider("fallback")
        primary.complete_chat = AsyncMock(
            side_effect=LLMTransportError("connection failed")
        )
        fallback.complete_chat = AsyncMock(return_value="fallback result")

        fp = FallbackProvider(primary, fallback)
        result = _run(fp.complete_chat([{"role": "user", "content": "hi"}]))

        assert result == "fallback result"

    def test_http_5xx_triggers_fallback(self):
        primary = _make_provider("primary")
        fallback = _make_provider("fallback")
        primary.complete_chat = AsyncMock(
            side_effect=LLMHTTPStatusError(502, "Bad Gateway")
        )
        fallback.complete_chat = AsyncMock(return_value="fallback result")

        fp = FallbackProvider(primary, fallback)
        result = _run(fp.complete_chat([{"role": "user", "content": "hi"}]))

        assert result == "fallback result"

    def test_http_429_triggers_fallback(self):
        primary = _make_provider("primary")
        fallback = _make_provider("fallback")
        primary.complete_chat = AsyncMock(
            side_effect=LLMHTTPStatusError(429, "Rate limited")
        )
        fallback.complete_chat = AsyncMock(return_value="fallback result")

        fp = FallbackProvider(primary, fallback)
        result = _run(fp.complete_chat([{"role": "user", "content": "hi"}]))

        assert result == "fallback result"

    def test_http_4xx_no_fallback(self):
        primary = _make_provider("primary")
        fallback = _make_provider("fallback")
        primary.complete_chat = AsyncMock(
            side_effect=LLMHTTPStatusError(401, "Unauthorized")
        )
        fallback.complete_chat = AsyncMock(return_value="fallback result")

        fp = FallbackProvider(primary, fallback)

        with pytest.raises(LLMHTTPStatusError) as exc_info:
            _run(fp.complete_chat([{"role": "user", "content": "hi"}]))

        assert exc_info.value.status_code == 401
        fallback.complete_chat.assert_not_called()

    def test_format_error_no_fallback(self):
        primary = _make_provider("primary")
        fallback = _make_provider("fallback")
        primary.complete_chat = AsyncMock(
            side_effect=LLMResponseFormatError("bad json")
        )
        fallback.complete_chat = AsyncMock(return_value="fallback result")

        fp = FallbackProvider(primary, fallback)

        with pytest.raises(LLMResponseFormatError):
            _run(fp.complete_chat([{"role": "user", "content": "hi"}]))

        fallback.complete_chat.assert_not_called()

    def test_both_fail_raises_all_providers_failed(self):
        primary = _make_provider("primary")
        fallback = _make_provider("fallback")
        primary.complete_chat = AsyncMock(
            side_effect=LLMTransportError("primary down")
        )
        fallback.complete_chat = AsyncMock(
            side_effect=LLMTransportError("fallback down")
        )

        fp = FallbackProvider(primary, fallback)

        with pytest.raises(LLMAllProvidersFailed) as exc_info:
            _run(fp.complete_chat([{"role": "user", "content": "hi"}]))

        assert exc_info.value.primary_error is not None
        assert exc_info.value.fallback_error is not None

    def test_primary_transport_no_fallback_available(self):
        primary = _make_provider("primary")
        fallback = _make_provider("fallback")
        fallback.is_available = lambda: False
        primary.complete_chat = AsyncMock(
            side_effect=LLMTransportError("primary down")
        )

        fp = FallbackProvider(primary, fallback)

        with pytest.raises(LLMAllProvidersFailed):
            _run(fp.complete_chat([{"role": "user", "content": "hi"}]))


# ---------------------------------------------------------------------------
# _should_fallback
# ---------------------------------------------------------------------------

class TestShouldFallback:

    def test_transport_error_true(self):
        assert _should_fallback(LLMTransportError("fail")) is True

    def test_http_5xx_true(self):
        assert _should_fallback(LLMHTTPStatusError(500, "fail")) is True

    def test_http_502_true(self):
        assert _should_fallback(LLMHTTPStatusError(502, "fail")) is True

    def test_http_429_true(self):
        assert _should_fallback(LLMHTTPStatusError(429, "fail")) is True

    def test_http_408_true(self):
        assert _should_fallback(LLMHTTPStatusError(408, "fail")) is True

    def test_http_400_false(self):
        assert _should_fallback(LLMHTTPStatusError(400, "fail")) is False

    def test_http_401_false(self):
        assert _should_fallback(LLMHTTPStatusError(401, "fail")) is False

    def test_http_403_false(self):
        assert _should_fallback(LLMHTTPStatusError(403, "fail")) is False

    def test_http_404_false(self):
        assert _should_fallback(LLMHTTPStatusError(404, "fail")) is False

    def test_format_error_false(self):
        assert _should_fallback(LLMResponseFormatError("bad json")) is False

    def test_batch_all_transport_true(self):
        err = LLMBatchAggregateError(
            [LLMTransportError("a"), LLMTransportError("b")],
            provider_label="test",
        )
        assert _should_fallback(err) is True

    def test_batch_mixed_errors_false(self):
        err = LLMBatchAggregateError(
            [LLMTransportError("a"), LLMResponseFormatError("b")],
            provider_label="test",
        )
        assert _should_fallback(err) is False


# ---------------------------------------------------------------------------
# Error sanitization
# ---------------------------------------------------------------------------

class TestErrorSanitization:

    def test_sanitizes_api_key(self):
        msg = "Authorization: Bearer sk-abc123def456ghi789"
        sanitized = _sanitize_error_message(msg)
        assert "sk-abc123def456ghi789" not in sanitized
        assert "sk-***" in sanitized

    def test_sanitizes_key_prefix(self):
        msg = "key-abcdefghijklmnop"
        sanitized = _sanitize_error_message(msg)
        assert "key-abcdefghijklmnop" not in sanitized
        assert "key-***" in sanitized

    def test_preserves_normal_text(self):
        msg = "Connection refused to api.openai.com"
        sanitized = _sanitize_error_message(msg)
        assert sanitized == msg

    def test_all_providers_failed_sanitized(self):
        primary_exc = LLMTransportError("sk-realkey12345678 failed")
        err = LLMAllProvidersFailed(primary_exc)
        assert "sk-realkey12345678" not in str(err)
        assert "sk-***" in str(err)


# ---------------------------------------------------------------------------
# LLMHTTPStatusError properties
# ---------------------------------------------------------------------------

class TestLLMHTTPStatusError:

    def test_stores_status_code(self):
        err = LLMHTTPStatusError(502, "Bad Gateway")
        assert err.status_code == 502

    def test_message(self):
        err = LLMHTTPStatusError(500, "Internal Server Error")
        assert "Internal Server Error" in str(err)

    def test_should_fallback_property(self):
        for code in (408, 429, 500, 502, 503, 504):
            assert LLMHTTPStatusError(code, "").should_fallback is True
        for code in (400, 401, 403, 404):
            assert LLMHTTPStatusError(code, "").should_fallback is False


# ---------------------------------------------------------------------------
# LLMBatchAggregateError
# ---------------------------------------------------------------------------

class TestLLMBatchAggregateError:

    def test_all_transport_property(self):
        err = LLMBatchAggregateError(
            [LLMTransportError("a")],
            provider_label="test",
        )
        assert err.all_transport_errors is True

    def test_mixed_not_all_transport(self):
        err = LLMBatchAggregateError(
            [LLMTransportError("a"), LLMHTTPStatusError(400, "b")],
            provider_label="test",
        )
        assert err.all_transport_errors is False

    def test_sanitizes_failures(self):
        err = LLMBatchAggregateError(
            [LLMTransportError("sk-secret1234567890 error")],
            provider_label="test",
        )
        assert "sk-secret1234567890" not in str(err)
