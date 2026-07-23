"""
LLM Translation Provider abstraction for tag localization.
Supports OpenAI-compatible APIs with optional fallback provider.
Defaults to disabled when no API key is configured.

Chunking: large batches are split into chunks of CHUNK_SIZE tags per API call
to avoid exceeding LLM token limits.  Errors in individual chunks are collected
and re-raised so callers can report them properly.

Two-layer completion API:
  complete_chat()  — returns the raw content string from the LLM.
  complete_json()  — calls complete_chat(), then parses as JSON.
"""
import json
import logging
import os
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

CHUNK_SIZE = 25

_FALLBACK_HTTP_CODES = {408, 429, 500, 502, 503, 504}
_LLM_TRANSPORT_LOGGER_NAMES = (
    "httpx",
    "httpcore",
    "openai",
    "openai._base_client",
    "urllib3",
    "aiohttp",
    "app.services.llm_translation_provider",
    __name__,
)
_LLM_LOG_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(authorization|proxy-authorization|cookie|set-cookie|"
    r"x-api-key|api[_-]?key|access[_-]?token|refresh[_-]?token)\b"
    r"\s*[:=]\s*(?:bearer\s+[A-Za-z0-9._~+/=-]+|"
    r"\"[^\"]*\"|'[^']*'|[^\s,;}\]]+)"
)
_LLM_LOG_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_LLM_LOG_KEY_RE = re.compile(r"\b(?:sk-|key-)[A-Za-z0-9_-]{8,}\b")
_LLM_LOG_HEADERS_RE = re.compile(
    r"(?is)\b(?:request|response)?_?headers?\s*[:=]\s*(?:\{.*?\}|\[.*?\]|\(.*?\))"
)
_LLM_LOG_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


def redact_llm_transport_log_text(value: Any) -> str:
    """Redact credentials, headers, and endpoint material from LLM transport logs."""

    text_value = str(value)
    text_value = _LLM_LOG_HEADERS_RE.sub("headers=[REDACTED]", text_value)
    text_value = _LLM_LOG_SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}=[REDACTED]", text_value
    )
    text_value = _LLM_LOG_BEARER_RE.sub("Bearer [REDACTED]", text_value)
    text_value = _LLM_LOG_KEY_RE.sub("[REDACTED_API_KEY]", text_value)
    return _LLM_LOG_URL_RE.sub("[REDACTED_ENDPOINT]", text_value)


class LLMTransportRedactionFilter(logging.Filter):
    """Sanitize a LogRecord before any configured process handler sees it."""

    _violet_llm_transport_redaction_filter = True

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "_violet_llm_transport_log_redacted", False):
            return True
        record.msg = redact_llm_transport_log_text(record.getMessage())
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        record._violet_llm_transport_log_redacted = True
        return True


def _install_llm_redaction_filter(target: Any) -> None:
    if any(
        getattr(value, "_violet_llm_transport_redaction_filter", False)
        for value in target.filters
    ):
        return
    target.addFilter(LLMTransportRedactionFilter())


def _install_process_log_record_redaction() -> bool:
    """Redact every subsequently created LogRecord before any handler sees it."""

    current_factory = logging.getLogRecordFactory()
    if getattr(
        current_factory,
        "_violet_llm_transport_redaction_factory",
        False,
    ):
        return True

    redaction_filter = LLMTransportRedactionFilter()

    def redacting_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = current_factory(*args, **kwargs)
        redaction_filter.filter(record)
        return record

    redacting_factory._violet_llm_transport_redaction_factory = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(redacting_factory)
    return True


def harden_llm_transport_logging() -> dict[str, Any]:
    """Disable request chatter and redact every current process log handler."""

    hardened_loggers = []
    for name in _LLM_TRANSPORT_LOGGER_NAMES:
        current = logging.getLogger(name)
        current.setLevel(logging.WARNING)
        _install_llm_redaction_filter(current)
        for handler in current.handlers:
            _install_llm_redaction_filter(handler)
        hardened_loggers.append(name)
    handler_count = 0
    for handler in logging.getLogger().handlers:
        _install_llm_redaction_filter(handler)
        handler_count += 1
    process_factory_redaction = _install_process_log_record_redaction()
    return {
        "policy_version": "violet_llm_transport_log_redaction_v1",
        "minimum_log_level": "WARNING",
        "hardened_logger_names": sorted(set(hardened_loggers)),
        "process_handler_count": handler_count,
        "process_log_record_factory_redaction_enabled": process_factory_redaction,
        "request_response_body_logging_enabled": False,
    }


def _safe_url_host(url: str) -> str:
    try:
        return urlparse(url).hostname or url
    except Exception:
        return url


def _detect_proxy() -> Optional[str]:
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        val = os.environ.get(key)
        if val:
            try:
                parsed = urlparse(val)
                return f"{parsed.hostname}:{parsed.port}" if parsed.port else parsed.hostname
            except Exception:
                return val
    return None


def _format_error(exc: Exception, *, provider_label: str, base_url: str, model: str) -> str:
    proxy = _detect_proxy()
    parts = [
        f"[{type(exc).__name__}]",
        f"provider={provider_label}",
        f"host={_safe_url_host(base_url)}",
        f"model={model}",
    ]
    if proxy:
        parts.append(f"proxy={proxy}")
    parts.append(repr(exc) if not str(exc) else str(exc))
    return " ".join(parts)


_TRANSPORT_ERRORS = (
    "ConnectError", "ConnectTimeout", "ReadTimeout", "WriteTimeout",
    "PoolTimeout", "RemoteProtocolError", "LocalProtocolError",
    "NetworkError", "TimeoutException",
)


def _is_transport_error(exc: Exception) -> bool:
    return type(exc).__name__ in _TRANSPORT_ERRORS


def _sanitize_error_message(msg: str) -> str:
    """Remove anything that looks like an API key from error messages."""
    legacy_safe = re.sub(
        r"\b(sk-|key-)[a-zA-Z0-9_-]{8,}",
        r"\1***",
        msg,
    )
    placeholders = sorted(set(re.findall(r"\b(?:sk-|key-)\*{3}", legacy_safe)))
    sanitized = redact_llm_transport_log_text(legacy_safe)
    for placeholder in placeholders:
        if placeholder not in sanitized:
            sanitized = f"{sanitized} {placeholder}"
    return sanitized


def _strip_json_code_fence(content: str) -> str:
    stripped = content.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if not lines:
        return stripped
    if lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return "\n".join(lines[1:]).strip()


def _first_balanced_json_slice(content: str) -> Optional[str]:
    """Return the first balanced JSON object/array substring in model text."""

    for start, char in enumerate(content):
        if char not in "{[":
            continue
        stack = [char]
        in_string = False
        escaped = False
        for index in range(start + 1, len(content)):
            current = content[index]
            if in_string:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    in_string = False
                continue
            if current == '"':
                in_string = True
            elif current in "{[":
                stack.append(current)
            elif current in "}]":
                if not stack:
                    break
                opener = stack[-1]
                if (opener == "{" and current != "}") or (opener == "[" and current != "]"):
                    break
                stack.pop()
                if not stack:
                    return content[start:index + 1]
    return None


def _loads_json_from_model_text(content: str) -> Any:
    """Parse common OpenAI-compatible JSON response shapes."""

    candidates = [content.strip(), _strip_json_code_fence(content)]
    balanced = _first_balanced_json_slice(content)
    if balanced:
        candidates.append(balanced)
    seen: set[str] = set()
    last_error: Exception | None = None
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise ValueError("empty LLM JSON response")


# ── Structured error hierarchy ──────────────────────────────────


class LLMProviderError(RuntimeError):
    """Base class for all LLM provider errors."""


class LLMTransportError(LLMProviderError):
    """Network / transport-layer error — triggers fallback."""


class LLMHTTPStatusError(LLMProviderError):
    """HTTP status-code error from the upstream LLM API."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(message)

    @property
    def should_fallback(self) -> bool:
        return self.status_code in _FALLBACK_HTTP_CODES


class LLMResponseFormatError(LLMProviderError):
    """Model returned content that could not be parsed as expected — no fallback."""


class LLMAllProvidersFailed(LLMProviderError):
    """Both primary and fallback providers failed."""

    def __init__(self, primary_error: Exception, fallback_error: Optional[Exception] = None):
        self.primary_error = primary_error
        self.fallback_error = fallback_error
        parts = [f"primary: {_sanitize_error_message(repr(primary_error))}"]
        if fallback_error:
            parts.append(f"fallback: {_sanitize_error_message(repr(fallback_error))}")
        super().__init__(f"All LLM providers failed — {'; '.join(parts)}")


class LLMBatchAggregateError(LLMProviderError):
    """Raised when all chunks in a batch fail. Carries structured failure info."""

    _BATCH_FALLBACK_HTTP_CODES = _FALLBACK_HTTP_CODES

    @staticmethod
    def _is_failure_fallback_eligible(exc: Exception) -> bool:
        if isinstance(exc, LLMTransportError) or _is_transport_error(exc):
            return True
        if isinstance(exc, LLMHTTPStatusError):
            return exc.should_fallback
        return False

    def __init__(self, failures: list, *, provider_label: str):
        self.failures = failures
        self.provider_label = provider_label
        self._all_transport = all(
            isinstance(f, LLMTransportError) or _is_transport_error(f) for f in failures
        )
        self._all_fallback_eligible = all(
            self._is_failure_fallback_eligible(f) for f in failures
        )
        sanitized = _sanitize_error_message(
            "; ".join(f"{type(f).__name__}: {repr(f)}" for f in failures)
        )
        super().__init__(
            f"All LLM chunks failed ({provider_label}): {sanitized}"
        )

    @property
    def all_fallback_eligible_errors(self) -> bool:
        return self._all_fallback_eligible

    @property
    def all_transport_errors(self) -> bool:
        return self._all_transport

    @property
    def has_non_transport_errors(self) -> bool:
        return not self._all_transport


# Keep old name as alias for backwards compat in tests
LLMChunkAggregateError = LLMBatchAggregateError


def _is_fallback_eligible_provider_error(exc: Exception) -> bool:
    """Check if a single provider-level error is fallback-eligible."""
    if isinstance(exc, LLMTransportError) or _is_transport_error(exc):
        return True
    if isinstance(exc, LLMHTTPStatusError):
        return exc.should_fallback
    if isinstance(exc, LLMResponseFormatError):
        return False
    return False


def _should_fallback(exc: Exception) -> bool:
    """Determine whether an exception warrants trying the fallback provider."""
    if isinstance(exc, LLMBatchAggregateError):
        return exc.all_fallback_eligible_errors
    return _is_fallback_eligible_provider_error(exc)


# ── Result dataclass ────────────────────────────────────────────


class TranslationResult:
    def __init__(self, canonical_name: str, display_name_zh: str,
                 aliases_zh: List[str] = None, notes: str = "",
                 needs_review: bool = True, category: str = "general"):
        self.canonical_name = canonical_name
        self.display_name_zh = display_name_zh
        self.aliases_zh = aliases_zh or []
        self.notes = notes
        self.needs_review = needs_review
        self.category = category


# ── Provider abstraction ────────────────────────────────────────


class BaseLLMProvider(ABC):
    @abstractmethod
    def is_available(self) -> bool:
        ...

    @abstractmethod
    def get_provider_name(self) -> str:
        ...

    @abstractmethod
    async def complete_chat(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        """Raw chat completion. Returns the content string."""
        ...

    async def complete_json(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> Any:
        """Chat completion parsed as JSON. Raises LLMResponseFormatError on parse failure."""
        content = await self.complete_chat(
            messages, temperature=temperature, max_tokens=max_tokens,
        )
        try:
            return _loads_json_from_model_text(content)
        except (json.JSONDecodeError, ValueError) as e:
            sample = _sanitize_error_message(content[:200]).replace("\n", "\\n")
            raise LLMResponseFormatError(
                f"Failed to parse LLM response as JSON: {e} - content[:200]: {sample}"
            ) from e

    @abstractmethod
    async def translate_tags(self, tags: List[Dict[str, str]]) -> List[TranslationResult]:
        """Translate a batch of tags. Each tag dict has 'name' and 'category'."""
        ...


class DisabledProvider(BaseLLMProvider):
    def is_available(self) -> bool:
        return False

    def get_provider_name(self) -> str:
        return "disabled"

    async def complete_chat(self, messages, *, temperature=0.3, max_tokens=4096) -> str:
        raise LLMProviderError("LLM provider is disabled")

    async def translate_tags(self, tags: List[Dict[str, str]]) -> List[TranslationResult]:
        return []


class OpenAICompatibleProvider(BaseLLMProvider):
    def __init__(self, api_key: str, model: str, base_url: str, *, label: str = "primary"):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.label = label
        self.last_completion_content = ""
        self.last_usage: Dict[str, int] = {}
        self.usage_totals: Dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    def is_available(self) -> bool:
        return bool(self.api_key and self.model and self.base_url)

    def get_provider_name(self) -> str:
        return f"openai_compatible({self.label})"

    async def complete_chat(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        """Raw OpenAI-compatible chat completion. Returns the content string.

        Raises LLMTransportError for network issues, LLMHTTPStatusError for bad status codes.
        """
        harden_llm_transport_logging()
        import httpx

        if not self.is_available():
            raise LLMProviderError(f"Provider {self.label} is not available")

        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    },
                )
        except Exception as e:
            if _is_transport_error(e):
                raise LLMTransportError(
                    _format_error(e, provider_label=self.label, base_url=self.base_url, model=self.model)
                ) from e
            raise

        if resp.status_code != 200:
            msg = _format_error(
                Exception(f"HTTP {resp.status_code}: {resp.text[:300]}"),
                provider_label=self.label,
                base_url=self.base_url,
                model=self.model,
            )
            raise LLMHTTPStatusError(resp.status_code, msg)

        data = resp.json()
        usage = data.get("usage") if isinstance(data, dict) else None
        normalized_usage: Dict[str, int] = {}
        if isinstance(usage, dict):
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = usage.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    normalized_usage[key] = value
        self.last_usage = normalized_usage
        for key in self.usage_totals:
            self.usage_totals[key] += int(normalized_usage.get(key, 0))
        self.last_completion_content = data["choices"][0]["message"]["content"].strip()
        return self.last_completion_content

    async def translate_tags(self, tags: List[Dict[str, str]]) -> List[TranslationResult]:
        if not self.is_available():
            return []

        all_results: List[TranslationResult] = []
        chunk_exceptions: List[Exception] = []

        for i in range(0, len(tags), CHUNK_SIZE):
            chunk = tags[i:i + CHUNK_SIZE]
            try:
                chunk_results = await self._translate_chunk(chunk)
                all_results.extend(chunk_results)
            except Exception as e:
                msg = _format_error(e, provider_label=self.label, base_url=self.base_url, model=self.model)
                logger.error("LLM chunk %d failed: %s", i // CHUNK_SIZE + 1, msg)
                chunk_exceptions.append(e)

        if chunk_exceptions and not all_results:
            raise LLMBatchAggregateError(chunk_exceptions, provider_label=self.label)

        if chunk_exceptions:
            logger.warning("LLM translation partially failed (%s): %d chunk(s)", self.label, len(chunk_exceptions))

        return all_results

    async def _translate_chunk(self, tags: List[Dict[str, str]]) -> List[TranslationResult]:
        tags_text = json.dumps(tags, ensure_ascii=False)
        system_prompt = (
            "You are a Danbooru anime tag translator. Translate the given tags to Chinese (zh-CN).\n"
            "Rules:\n"
            "- general tags: translate to natural Chinese (e.g. blue_eyes → 蓝眼睛)\n"
            "- character tags: use the commonly known Chinese name if exists; if unsure, keep original and set needs_review=true\n"
            "- copyright tags: use the Chinese title if exists; if unsure, keep original and set needs_review=true\n"
            "- artist tags: usually keep original name, set needs_review=true\n"
            "- meta/rating tags: translate descriptively\n"
            "\nRespond with a JSON array. Each element:\n"
            '{"canonical_name": "...", "display_name_zh": "...", "aliases_zh": ["..."], "notes": "...", "needs_review": true/false}\n'
            "ONLY output valid JSON array, no markdown, no explanation."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Translate these Danbooru tags to Chinese:\n{tags_text}"},
        ]

        results_raw = await self.complete_json(messages)

        if not isinstance(results_raw, list):
            raise LLMResponseFormatError(f"LLM returned non-array response: {str(results_raw)[:200]}")

        results = []
        for item in results_raw:
            if not isinstance(item, dict):
                continue
            cn = item.get("canonical_name", "")
            dn = item.get("display_name_zh", "")
            if not cn or not dn:
                continue
            cat = "general"
            for t in tags:
                if t["name"] == cn:
                    cat = t.get("category", "general")
                    break
            results.append(TranslationResult(
                canonical_name=cn,
                display_name_zh=dn,
                aliases_zh=item.get("aliases_zh", []),
                notes=item.get("notes", ""),
                needs_review=item.get("needs_review", True),
                category=cat,
            ))
        return results


class FallbackProvider(BaseLLMProvider):
    """Wraps a primary and fallback provider. On fallback-eligible errors, retries with fallback."""

    def __init__(self, primary: OpenAICompatibleProvider, fallback: OpenAICompatibleProvider):
        self.primary = primary
        self.fallback = fallback

    def is_available(self) -> bool:
        return self.primary.is_available() or self.fallback.is_available()

    def get_provider_name(self) -> str:
        return f"fallback({self.primary.label}->{self.fallback.label})"

    async def complete_chat(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        if self.primary.is_available():
            try:
                return await self.primary.complete_chat(
                    messages, temperature=temperature, max_tokens=max_tokens,
                )
            except Exception as primary_exc:
                if _should_fallback(primary_exc):
                    logger.warning(
                        "Primary provider (%s) failed (%s), trying fallback (%s)",
                        self.primary.label, type(primary_exc).__name__, self.fallback.label,
                    )
                    if self.fallback.is_available():
                        try:
                            return await self.fallback.complete_chat(
                                messages, temperature=temperature, max_tokens=max_tokens,
                            )
                        except Exception as fallback_exc:
                            raise LLMAllProvidersFailed(primary_exc, fallback_exc) from fallback_exc
                    raise LLMAllProvidersFailed(primary_exc) from primary_exc
                raise

        if self.fallback.is_available():
            return await self.fallback.complete_chat(
                messages, temperature=temperature, max_tokens=max_tokens,
            )

        raise LLMProviderError("No LLM providers available")

    async def translate_tags(self, tags: List[Dict[str, str]]) -> List[TranslationResult]:
        if self.primary.is_available():
            try:
                return await self.primary.translate_tags(tags)
            except Exception as primary_exc:
                if _should_fallback(primary_exc):
                    logger.warning(
                        "Primary provider (%s) batch failure (%s), trying fallback (%s)",
                        self.primary.label, type(primary_exc).__name__, self.fallback.label,
                    )
                    if self.fallback.is_available():
                        return await self.fallback.translate_tags(tags)
                raise

        if self.fallback.is_available():
            return await self.fallback.translate_tags(tags)

        return []


def get_llm_provider() -> BaseLLMProvider:
    harden_llm_transport_logging()
    from ..config import settings
    if not settings.TAG_TRANSLATION_LLM_ENABLED:
        return DisabledProvider()

    provider_type = settings.TAG_TRANSLATION_LLM_PROVIDER
    if provider_type != "openai_compatible":
        logger.warning("Unknown LLM provider: %s, using disabled", provider_type)
        return DisabledProvider()

    primary = OpenAICompatibleProvider(
        api_key=settings.TAG_TRANSLATION_LLM_API_KEY,
        model=settings.TAG_TRANSLATION_LLM_MODEL,
        base_url=settings.TAG_TRANSLATION_LLM_BASE_URL,
        label="primary",
    )

    fallback_key = settings.TAG_TRANSLATION_LLM_FALLBACK_API_KEY
    fallback_model = settings.TAG_TRANSLATION_LLM_FALLBACK_MODEL
    fallback_url = settings.TAG_TRANSLATION_LLM_FALLBACK_BASE_URL
    fallback_provider_type = settings.TAG_TRANSLATION_LLM_FALLBACK_PROVIDER

    if settings.TAG_TRANSLATION_LLM_FALLBACK_ENABLED and fallback_key and fallback_model and fallback_url:
        if fallback_provider_type not in {"openai_compatible", "deepseek"}:
            logger.warning("Unknown LLM fallback provider: %s; fallback disabled", fallback_provider_type)
            return primary
        fallback = OpenAICompatibleProvider(
            api_key=fallback_key,
            model=fallback_model,
            base_url=fallback_url,
            label="fallback",
        )
        logger.info(
            "LLM fallback configured: %s -> %s",
            _safe_url_host(settings.TAG_TRANSLATION_LLM_BASE_URL),
            _safe_url_host(fallback_url),
        )
        return FallbackProvider(primary, fallback)

    return primary
