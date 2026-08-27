"""Conservative creator-identity policy shared by resolver and SV1B proofs.

Names remain searchable evidence, but they are never identity truth by
themselves. A creator identity union requires a stable provider account key
or an explicitly audited authoritative relationship.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .pixiv_identity_policy import canonical_pixiv_creator_id
from .source_metadata_registry_service import canonical_source_key


CREATOR_IDENTITY_POLICY_VERSION = "sv1b_conservative_creator_identity_v1"

_PLACEHOLDER_CREATOR_KEYS = frozenset(
    {
        "anonymous",
        "deleted",
        "hidden",
        "no_name",
        "noname",
        "private",
        "unknown",
        "user",
    }
)


def is_placeholder_creator_name(value: Any) -> bool:
    """Return true for labels that cannot serve as creator identity aliases."""

    key = canonical_source_key(value)
    return not key or key in _PLACEHOLDER_CREATOR_KEYS


def stable_creator_identity_key(signal: Any) -> str | None:
    """Return an auditable provider identity key, never a name-derived key."""

    payload = getattr(signal, "evidence_payload", None) or {}
    if not isinstance(payload, Mapping):
        return None
    provider = str(getattr(signal, "provider", None) or "").strip().casefold()
    stable_id_value = payload.get("stable_creator_id")
    if stable_id_value in (None, ""):
        stable_id_value = payload.get("creator_id")
    if provider == "pixiv":
        stable_id = canonical_pixiv_creator_id(stable_id_value)
        return f"provider-account:pixiv:{stable_id}" if stable_id else None

    raw_value = getattr(signal, "raw_value", None)
    display_value = getattr(signal, "display_value", None)
    if is_placeholder_creator_name(display_value or raw_value):
        return None
    stable_fingerprint = str(payload.get("stable_identity_fingerprint") or "").strip()
    if stable_fingerprint:
        return f"stable-fingerprint:{stable_fingerprint}"
    stable_id = str(stable_id_value or "").strip()
    if provider and stable_id:
        return f"provider-account:{provider}:{stable_id}"
    authority = str(payload.get("authoritative_identity_reference") or "").strip()
    if authority and payload.get("authoritative_identity_verified") is True:
        return f"authority:{authority}"
    return None


def creator_identity_union_verdict(left: Any, right: Any) -> dict[str, Any]:
    """Classify a creator pair without using media count or name similarity."""

    left_key = stable_creator_identity_key(left)
    right_key = stable_creator_identity_key(right)
    allowed = bool(left_key and left_key == right_key)
    return {
        "policy_version": CREATOR_IDENTITY_POLICY_VERSION,
        "identity_union_allowed": allowed,
        "reason_code": (
            "shared_auditable_stable_creator_identity"
            if allowed
            else "creator_identity_strong_evidence_missing"
        ),
        "left_has_strong_identity": bool(left_key),
        "right_has_strong_identity": bool(right_key),
        "shared_identity_key": left_key if allowed else None,
        "media_count_used_as_identity_evidence": False,
        "string_similarity_used_as_identity_evidence": False,
    }
