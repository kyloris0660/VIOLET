"""Canonical Pixiv identity validation shared by ingestion and projection.

Pixiv work and creator identities are positive, canonical numeric identifiers.
Display names and account handles are observations and never pass this seam.
"""

from __future__ import annotations

import re
from typing import Any


PIXIV_IDENTITY_POLICY_VERSION = "scv2_px1_pixiv_identity_v1"
PIXIV_NUMERIC_ID_PATTERN = re.compile(r"[1-9]\d{0,11}\Z")
PIXIV_PROVIDER_MARKER_ALLOWLIST = frozenset({"pixiv"})


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


def is_allowlisted_pixiv_provider_marker(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return value.strip().casefold() in PIXIV_PROVIDER_MARKER_ALLOWLIST
