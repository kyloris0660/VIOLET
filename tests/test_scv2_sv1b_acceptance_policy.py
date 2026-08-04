from __future__ import annotations

from types import SimpleNamespace

from app.services.creator_identity_policy import (
    CREATOR_IDENTITY_POLICY_VERSION,
    creator_identity_union_verdict,
    is_placeholder_creator_name,
)
from app.services.tag_localization_policy import (
    LOCALIZATION_REVOCATION_POLICY_VERSION,
    MANUALLY_REVOKED_TRANSLATION_TAG_ORDER,
    effective_localization_disposition,
)
from app.services.tag_localization_service import get_tag_display_name
from app.utils.search_parser import (
    _translation_alias_trusted_for_search,
    resolve_zh_alias,
)


def _creator_signal(
    value: str,
    *,
    stable_id: str | None = None,
    provider: str = "pixiv",
    media_id: int | None = 1,
):
    payload = {"stable_creator_id": stable_id} if stable_id else {}
    return SimpleNamespace(
        raw_value=value,
        display_value=value,
        provider=provider,
        media_id=media_id,
        evidence_payload=payload,
    )


def test_placeholder_hidden_name_cannot_be_trusted_creator_alias() -> None:
    assert is_placeholder_creator_name("hidden") is True
    verdict = creator_identity_union_verdict(
        _creator_signal("hidden", stable_id="42"),
        _creator_signal("real-account", stable_id="42"),
    )
    assert verdict["policy_version"] == CREATOR_IDENTITY_POLICY_VERSION
    assert verdict["identity_union_allowed"] is False


def test_single_media_and_string_similarity_do_not_create_identity_union() -> None:
    verdict = creator_identity_union_verdict(
        _creator_signal("similar_name", media_id=7),
        _creator_signal("similar-name", media_id=7),
    )
    assert verdict["identity_union_allowed"] is False
    assert verdict["media_count_used_as_identity_evidence"] is False
    assert verdict["string_similarity_used_as_identity_evidence"] is False


def test_same_provider_stable_creator_id_is_strong_identity_evidence() -> None:
    verdict = creator_identity_union_verdict(
        _creator_signal("display-name", stable_id="42"),
        _creator_signal("account-name", stable_id="42"),
    )
    assert verdict["identity_union_allowed"] is True
    assert verdict["reason_code"] == "shared_auditable_stable_creator_identity"


def test_search_result_union_does_not_promote_creator_identity() -> None:
    left = _creator_signal("shared", media_id=1)
    right = _creator_signal("shared", media_id=2)
    left.evidence_payload["search_result_union"] = True
    right.evidence_payload["search_result_union"] = True
    assert creator_identity_union_verdict(left, right)[
        "identity_union_allowed"
    ] is False


def test_owner_revoked_symbol_localizations_use_canonical_fallback() -> None:
    for canonical in MANUALLY_REVOKED_TRANSLATION_TAG_ORDER:
        disposition = effective_localization_disposition(canonical)
        assert (
            disposition["policy_version"]
            == LOCALIZATION_REVOCATION_POLICY_VERSION
        )
        assert disposition["display_name"] == canonical
        assert (
            disposition["translation_status"]
            == "manual_localization_review_pending"
        )
        assert disposition["accepted_chinese_alias_exposed"] is False


def test_revoked_symbol_translation_is_not_displayed_or_searchable_alias() -> None:
    class NoDatabaseAccess:
        def query(self, *_args, **_kwargs):
            raise AssertionError("revoked canonical fallback must not query DB")

    rows = (
        {
            "canonical_name": r"\||/",
            "display_name": "无奈表情",
            "category": "general",
            "source": "llm",
            "status": "translated",
            "needs_review": False,
        },
        {
            "canonical_name": "<|>_<|>",
            "display_name": "眯眼表情",
            "category": "general",
            "source": "llm",
            "status": "translated",
            "needs_review": False,
        },
    )
    for row in rows:
        assert _translation_alias_trusted_for_search(row) is False
        assert resolve_zh_alias(row["display_name"]) == row["display_name"]
        assert (
            get_tag_display_name(NoDatabaseAccess(), row["canonical_name"])
            == row["canonical_name"]
        )
