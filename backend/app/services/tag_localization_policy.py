"""Versioned fail-closed display policy for manually rejected localizations."""

from __future__ import annotations

from typing import Any


LOCALIZATION_REVOCATION_POLICY_VERSION = "sv1b_manual_localization_revocation_v1"

# Owner-reviewed opaque symbol tags. Their historical LLM labels remain
# forensic evidence, but must not be displayed or used as Chinese aliases.
MANUALLY_REVOKED_TRANSLATION_TAG_ORDER = (r"\||/", "<|>_<|>")
MANUALLY_REVOKED_TRANSLATION_TAGS = frozenset(
    MANUALLY_REVOKED_TRANSLATION_TAG_ORDER
)
MANUALLY_REVOKED_DISPLAY_ALIASES = frozenset({"无奈表情", "眯眼表情"})


def is_translation_effectively_accepted(canonical_name: Any) -> bool:
    return str(canonical_name or "") not in MANUALLY_REVOKED_TRANSLATION_TAGS


def is_display_alias_manually_revoked(value: Any) -> bool:
    return str(value or "").strip() in MANUALLY_REVOKED_DISPLAY_ALIASES


def effective_localization_disposition(canonical_name: Any) -> dict[str, Any]:
    canonical = str(canonical_name or "")
    revoked = canonical in MANUALLY_REVOKED_TRANSLATION_TAGS
    return {
        "canonical_name": canonical,
        "display_name": canonical,
        "translation_status": (
            "manual_localization_review_pending"
            if revoked
            else "not_governed_by_manual_revocation"
        ),
        "canonical_fallback": revoked,
        "accepted_chinese_alias_exposed": False if revoked else None,
        "policy_version": LOCALIZATION_REVOCATION_POLICY_VERSION,
        "reason_code": (
            "owner_rejected_opaque_symbol_translation"
            if revoked
            else "not_manually_revoked"
        ),
    }
