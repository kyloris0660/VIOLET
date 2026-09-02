"""Canonical Pixiv identity validation shared by ingestion and projection.

Pixiv work and creator identities are positive, canonical numeric identifiers.
Display names and account handles are observations and never pass this seam.
"""

from __future__ import annotations

import re
from typing import Any, Mapping


PIXIV_IDENTITY_POLICY_VERSION = "scv2_px1_pixiv_identity_v1"
PIXIV_NUMERIC_ID_PATTERN = re.compile(r"[1-9]\d{0,11}\Z")
PIXIV_PAGE_INTEGER_PATTERN = re.compile(r"(?:0|[1-9]\d*)\Z")
PIXIV_PROVIDER_MARKER_ALLOWLIST = frozenset({"pixiv"})
PIXIV_PROVIDER_MARKER_FIELDS = (
    "provider",
    "extractor",
    "extractor_key",
    "category",
)
PIXIV_PROVIDER_MARKERS_BY_FIELD = {
    field: PIXIV_PROVIDER_MARKER_ALLOWLIST for field in PIXIV_PROVIDER_MARKER_FIELDS
}


def canonical_pixiv_numeric_identity(value: Any) -> str | None:
    """Return one canonical Pixiv numeric identity or ``None``.

    Strings must already be canonical.  In particular, whitespace, signs,
    leading zeroes, decimal notation, and booleans are rejected rather than
    silently coerced into a strong identity.
    """

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        candidate = str(value)
    elif isinstance(value, str):
        candidate = value
    else:
        return None
    return candidate if PIXIV_NUMERIC_ID_PATTERN.fullmatch(candidate) else None


def canonical_pixiv_work_id(value: Any) -> str | None:
    return canonical_pixiv_numeric_identity(value)


def canonical_pixiv_creator_id(value: Any) -> str | None:
    return canonical_pixiv_numeric_identity(value)


def _canonical_pixiv_page_integer(value: Any, *, positive: bool) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        candidate = str(value)
    elif isinstance(value, str):
        candidate = value
    else:
        return None
    if PIXIV_PAGE_INTEGER_PATTERN.fullmatch(candidate) is None:
        return None
    result = int(candidate)
    if positive and result == 0:
        return None
    return result


def canonical_pixiv_page_index(value: Any) -> int | None:
    """Return a non-negative canonical page index without numeric coercion."""

    return _canonical_pixiv_page_integer(value, positive=False)


def canonical_pixiv_page_count(value: Any) -> int | None:
    """Return a positive canonical page count without numeric coercion."""

    return _canonical_pixiv_page_integer(value, positive=True)


_MISSING_PAGE_VALUE = object()


def canonical_pixiv_page_domain(
    *,
    page_index: Any = _MISSING_PAGE_VALUE,
    page_count: Any = _MISSING_PAGE_VALUE,
) -> tuple[int, int | None] | None:
    """Validate the complete Pixiv page domain through one canonical seam.

    A genuinely absent page index keeps the historical page-zero default.
    Explicit nulls, booleans, floats, signs, whitespace, decimal notation, and
    leading-zero strings are invalid.  A supplied count must be positive and
    strictly greater than the page index.
    """

    if page_index is _MISSING_PAGE_VALUE:
        canonical_index = 0
    else:
        canonical_index = canonical_pixiv_page_index(page_index)
    if canonical_index is None:
        return None

    if page_count is _MISSING_PAGE_VALUE:
        canonical_count = None
    else:
        canonical_count = canonical_pixiv_page_count(page_count)
        if canonical_count is None:
            return None
    if canonical_count is not None and canonical_index >= canonical_count:
        return None
    return canonical_index, canonical_count


def canonical_pixiv_provider_marker(field: str, value: Any) -> str | None:
    """Normalize one known marker field by an exact field-specific allowlist."""

    allowlist = PIXIV_PROVIDER_MARKERS_BY_FIELD.get(field)
    if allowlist is None or not isinstance(value, str):
        return None
    normalized = value.strip().casefold()
    return "pixiv" if normalized in allowlist else None


def canonical_pixiv_provider_marker_consensus(
    markers: Mapping[str, Any],
) -> str | None:
    """Require every non-empty explicit gallery-dl marker to mean Pixiv."""

    resolved: list[str] = []
    for field in PIXIV_PROVIDER_MARKER_FIELDS:
        if field not in markers:
            continue
        value = markers[field]
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        marker = canonical_pixiv_provider_marker(field, value)
        if marker is None:
            return None
        resolved.append(marker)
    if not resolved or len(set(resolved)) != 1:
        return None
    return resolved[0]


def is_allowlisted_pixiv_provider_marker(value: Any) -> bool:
    return canonical_pixiv_provider_marker("provider", value) == "pixiv"
