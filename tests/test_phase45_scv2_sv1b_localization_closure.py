from __future__ import annotations

from dataclasses import dataclass
import io
import json
import logging
from pathlib import Path

import pytest

from app.services.llm_translation_provider import (
    FallbackProvider,
    OpenAICompatibleProvider,
    harden_llm_transport_logging,
)
from scripts import run_phase45_scv2_sv1b_localization_closure as closure


@dataclass
class _Translation:
    canonical_name: str
    display_name_zh: str
    aliases_zh: list[str]
    needs_review: bool = False


class _Provider:
    def __init__(self) -> None:
        self.calls = 0
        self.standard_calls = 0
        self.targeted_calls = 0
        self.targeted_temperatures = []
        self.targeted_messages = []
        self.usage_totals = {"total_tokens": 0}
        self.model = closure.APPROVED_MODEL
        self.last_completion_content = ""

    def is_available(self) -> bool:
        return True

    def get_provider_name(self) -> str:
        return "primary"

    async def translate_tags(self, rows):
        self.calls += 1
        self.standard_calls += 1
        self.usage_totals["total_tokens"] += 120
        return [
            _Translation(
                canonical_name=row["name"],
                display_name_zh=f"中文{index}",
                aliases_zh=[],
            )
            for index, row in enumerate(rows, 1)
        ]

    async def complete_chat(self, messages, *, temperature=0.3, max_tokens=4096):
        self.calls += 1
        self.targeted_calls += 1
        self.targeted_temperatures.append(temperature)
        self.targeted_messages.append(messages)
        self.usage_totals["total_tokens"] += 80
        user = json.loads(messages[-1]["content"])
        self.last_completion_content = json.dumps({
            "canonical_name": user["canonical_name"],
            "display_name_zh": "红帽",
            "aliases_zh": [],
            "needs_review": False,
        }, ensure_ascii=False)
        return self.last_completion_content


class _FailOnceProvider(_Provider):
    async def translate_tags(self, rows):
        self.calls += 1
        self.standard_calls += 1
        if self.calls == 1:
            raise RuntimeError("sensitive-material-must-not-reach-checkpoint")
        return await super().translate_tags(rows)


class _AmbiguousThenClearProvider(_Provider):
    async def translate_tags(self, rows):
        self.calls += 1
        self.standard_calls += 1
        self.usage_totals["total_tokens"] += 120
        ambiguous = self.calls == 1
        return [
            _Translation(
                canonical_name=row["name"],
                display_name_zh=f"澶嶆牳{index}",
                aliases_zh=[],
                needs_review=ambiguous,
            )
            for index, row in enumerate(rows, 1)
        ]


class _AlwaysAmbiguousProvider(_AmbiguousThenClearProvider):
    async def complete_chat(self, messages, *, temperature=0.3, max_tokens=4096):
        content = await super().complete_chat(
            messages, temperature=temperature, max_tokens=max_tokens
        )
        value = json.loads(content)
        value["needs_review"] = True
        self.last_completion_content = json.dumps(value, ensure_ascii=False)
        return self.last_completion_content


class _EchoThenClearProvider(_Provider):
    async def translate_tags(self, rows):
        self.calls += 1
        self.standard_calls += 1
        self.usage_totals["total_tokens"] += 120
        return [
            _Translation(
                canonical_name=row["name"],
                display_name_zh=(row["name"] if self.calls == 1 else "红帽"),
                aliases_zh=[],
            )
            for row in rows
        ]


class _MixedProvider(_Provider):
    async def translate_tags(self, rows):
        self.calls += 1
        self.standard_calls += 1
        self.usage_totals["total_tokens"] += 120
        return [
            _Translation(
                canonical_name=row["name"],
                display_name_zh=(
                    "红帽" if row["name"] == "red_hat" else row["name"]
                ),
                aliases_zh=[],
            )
            for row in rows
        ]


class _RawSecretProjectionProvider(_Provider):
    async def translate_tags(self, rows):
        results = await super().translate_tags(rows)
        secret = "".join(("fake", "-raw", "-secret", "-123456789"))
        self.last_completion_content = (
            f"Authorization: Bearer {secret} Cookie={secret} "
            "https://example.invalid/v1?q=secret"
        )
        return results


def test_build_manifest_separates_policy_exclusions_and_binds_cost(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "run"
    output.mkdir()
    (output / "provider-hardening-preflight.json").write_text(
        json.dumps({"passed": True}), encoding="utf-8"
    )
    (output / "acquisition-closure-and-package-proof.json").write_text(
        json.dumps({"passed": True}), encoding="utf-8"
    )
    (output / "localization-baseline-proof.json").write_text(
        json.dumps({"accepted_translation_state": {"fingerprint": "a" * 64}}),
        encoding="utf-8",
    )
    (output / "localization-vocabulary-private.json").write_text(
        json.dumps({"blocking_missing_ai_tags": ["hero_name", "red_hat", "blue_sky"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(closure.sv1b, "validate_owned_output_root", lambda *_args, **_kwargs: {"passed": True})
    categories = {"hero_name": "character", "red_hat": "general", "blue_sky": "general"}
    monkeypatch.setattr(closure, "_category_by_tag", lambda _database, _names: categories)

    manifest = closure.build_manifest(
        output,
        primary_database="blombooru_sv1b_primary_test",
        replay_database="blombooru_sv1b_replay_test",
    )
    assert manifest["initial_missing_count"] == 3
    assert manifest["eligible_translation_count"] == 2
    assert manifest["explicit_exclusion_count"] == 1
    assert manifest["explicit_exclusions"][0]["reason_code"] == "ai_proper_noun_signal_not_identity_truth"
    assert manifest["projected_cost_upper_bound_usd"] <= 10.0
    assert len(manifest["manifest_fingerprint"]) == 64


def test_build_manifest_stops_before_network_when_provider_gate_is_incomplete(
    tmp_path: Path, monkeypatch
) -> None:
    output = tmp_path / "run"
    output.mkdir()
    (output / "provider-hardening-preflight.json").write_text(
        json.dumps({"passed": False}), encoding="utf-8"
    )
    monkeypatch.setattr(closure.sv1b, "validate_owned_output_root", lambda *_args, **_kwargs: {"passed": True})
    with pytest.raises(closure.LocalizationClosureError, match="provider_hardening_gate_incomplete"):
        closure.build_manifest(
            output,
            primary_database="blombooru_sv1b_primary_test",
            replay_database="blombooru_sv1b_replay_test",
        )


def test_validate_translation_items_salvages_valid_rows_and_classifies_each_reason() -> None:
    expected = [
        {"canonical_name": "red_hat", "category": "general"},
        {"canonical_name": "blue_sky", "category": "general"},
        {"canonical_name": "green_hair", "category": "general"},
        {"canonical_name": "solo", "category": "general"},
        {"canonical_name": "smile", "category": "general"},
        {"canonical_name": "white_shirt", "category": "general"},
    ]
    result = closure._validate_translation_items(expected, [
        _Translation("red_hat", "红帽", ["红色帽子"]),
        _Translation("blue_sky", "blue sky", []),
        _Translation("green_hair", "", []),
        _Translation("solo", "单人", "not-a-list"),
        _Translation("smile", "微笑", [], needs_review=True),
        _Translation("white_shirt", "白衬衫", []),
        _Translation("white_shirt", "白色衬衫", []),
        _Translation("unexpected_tag", "意外", []),
    ])

    assert [row["canonical_name"] for row in result["accepted_rows"]] == ["red_hat"]
    assert result["per_reason_counts"] == {
        "accepted_translation": 1,
        "ambiguous_needs_review": 1,
        "duplicate_result": 1,
        "invalid_aliases": 1,
        "invalid_display": 1,
        "unexpected_result": 1,
        "untranslated_echo": 1,
    }
    assert len(result["expected_membership_fingerprint"]) == 64
    assert len(result["result_membership_fingerprint"]) == 64
    assert len(result["verdict_membership_fingerprint"]) == 64


def test_validate_translation_items_reports_missing_without_discarding_valid() -> None:
    expected = [
        {"canonical_name": "red_hat", "category": "general"},
        {"canonical_name": "blue_sky", "category": "general"},
    ]
    result = closure._validate_translation_items(
        expected, [_Translation("red_hat", "红帽", [])]
    )
    assert result["per_reason_counts"] == {
        "accepted_translation": 1,
        "missing_result": 1,
    }
    assert result["accepted_rows"][0]["canonical_name"] == "red_hat"
    assert result["unresolved_rows"][0]["canonical_name"] == "blue_sky"


def test_standard_validation_rejects_non_echo_english_display() -> None:
    result = closure._validate_translation_items(
        [{"canonical_name": "red_hat", "category": "general"}],
        [_Translation("red_hat", "scarlet hat", [])],
        require_han=True,
    )
    assert result["accepted_rows"] == []
    assert result["per_reason_counts"] == {"invalid_display": 1}


def _manifest(rows):
    batch = {
        "batch_id": "0001-test",
        "input_fingerprint": closure.sv1b.sha256_payload(rows),
        "rows": rows,
    }
    return {
        "manifest_fingerprint": "f" * 64,
        "initial_missing_count": len(rows),
        "eligible_translation_count": len(rows),
        "explicit_exclusions": [],
        "eligible_rows": rows,
        "batches": [batch],
        "projected_cost_upper_bound_usd": 0.1,
    }


def _install_execution_fakes(
    tmp_path: Path,
    monkeypatch,
    *,
    rows=None,
    accepted_translation_count=1,
    blocking_missing=None,
):
    output = tmp_path / "run"
    output.mkdir()
    rows = rows or [{"canonical_name": "red_hat", "category": "general"}]
    manifest = _manifest(rows)
    monkeypatch.setattr(closure, "build_manifest", lambda *_args, **_kwargs: manifest)
    applied = []
    monkeypatch.setattr(
        closure,
        "_apply_batch",
        lambda database, values: applied.append((
            database, tuple(row["canonical_name"] for row in values)
        )) or {"inserted": len(values), "reused": 0},
    )
    remaining = list(blocking_missing or [])
    monkeypatch.setattr(
        closure.sv1b,
        "_vocabulary_state",
        lambda _database: (
            {"blocking_missing_ai_translation_count": len(remaining)},
            {"blocking_missing_ai_tags": remaining},
        ),
    )
    state = {"count": accepted_translation_count, "fingerprint": "s" * 64}
    monkeypatch.setattr(
        closure.sv1b, "_translation_logical_state", lambda _database: state
    )
    (output / "localization-baseline-proof.json").write_text(
        json.dumps({
            "accepted_translation_state": {
                "count": 0,
                "fingerprint": "b" * 64,
            }
        }),
        encoding="utf-8",
    )
    return output, manifest, applied


def test_execute_is_checkpoint_resumable_and_applies_both_databases(
    tmp_path: Path, monkeypatch
) -> None:
    output, _manifest_value, applied = _install_execution_fakes(
        tmp_path, monkeypatch
    )
    provider = _Provider()
    first = closure.execute(
        output,
        primary_database="blombooru_sv1b_primary_test",
        replay_database="blombooru_sv1b_replay_test",
        provider=provider,
    )
    second = closure.execute(
        output,
        primary_database="blombooru_sv1b_primary_test",
        replay_database="blombooru_sv1b_replay_test",
        provider=provider,
    )
    assert first["localization_complete"] is True
    assert second["localization_complete"] is True
    assert provider.standard_calls == 1
    assert provider.targeted_calls == 0
    assert [database for database, _ in applied] == [
        "blombooru_sv1b_primary_test",
        "blombooru_sv1b_replay_test",
    ]
    checkpoint = json.loads(
        (output / "localization/localization-llm-checkpoint-private.json")
        .read_text(encoding="utf-8")
    )
    assert checkpoint["batches"]["0001-test"]["status"] == "applied_both"


def test_execute_forbids_fallback_provider_before_any_call(
    tmp_path: Path, monkeypatch
) -> None:
    output = tmp_path / "run"
    output.mkdir()
    monkeypatch.setattr(
        closure,
        "build_manifest",
        lambda *_args, **_kwargs: {
            "manifest_fingerprint": "f" * 64,
            "eligible_translation_count": 1,
            "batches": [],
        },
    )
    with pytest.raises(
        closure.LocalizationClosureError, match="fallback_provider_forbidden"
    ):
        closure.execute(
            output,
            primary_database="blombooru_sv1b_primary_test",
            replay_database="blombooru_sv1b_replay_test",
            provider=FallbackProvider(object(), object()),
        )
    provider = _Provider()
    provider.model = "unapproved-model"
    with pytest.raises(closure.LocalizationClosureError, match="unapproved_model"):
        closure.execute(
            output,
            primary_database="blombooru_sv1b_primary_test",
            replay_database="blombooru_sv1b_replay_test",
            provider=provider,
        )


def test_primary_route_gate_accepts_real_primary_provider_identity_only() -> None:
    primary = OpenAICompatibleProvider(
        api_key="test-key",
        model=closure.APPROVED_MODEL,
        base_url="https://example.invalid/v1",
        label="primary",
    )
    fallback = OpenAICompatibleProvider(
        api_key="test-key",
        model=closure.APPROVED_MODEL,
        base_url="https://example.invalid/v1",
        label="fallback",
    )
    assert primary.get_provider_name() == "openai_compatible(primary)"
    assert closure._is_approved_primary_provider_route(primary) is True
    assert closure._is_approved_primary_provider_route(fallback) is False


def test_transport_logging_hardening_redacts_all_current_process_handlers() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        proof = harden_llm_transport_logging()
        secret = "".join(("fake", "-authorization", "-value", "-123456789"))
        cookie = "".join(("fake", "-cookie", "-value", "-123456789"))
        for name in proof["hardened_logger_names"]:
            logging.getLogger(name).warning(
                "Authorization: Bearer %s Cookie=%s Set-Cookie=%s "
                "headers={'Authorization':'%s'} "
                "endpoint=https://example.invalid/v1?q=%s",
                secret, cookie, cookie, secret, secret,
            )
        value = stream.getvalue()
        assert secret not in value
        assert cookie not in value
        assert "example.invalid" not in value
        assert "[REDACTED" in value
        assert proof["process_log_record_factory_redaction_enabled"] is True
        assert proof["request_response_body_logging_enabled"] is False

        late_stream = io.StringIO()
        late_handler = logging.StreamHandler(late_stream)
        late_logger = logging.getLogger("application.http.transport.created_late")
        late_logger.addHandler(late_handler)
        late_logger.propagate = False
        try:
            late_logger.warning(
                "Authorization: Bearer %s Cookie=%s endpoint=%s",
                secret,
                cookie,
                "https://example.invalid/v1?q=credential-material",
            )
            late_value = late_stream.getvalue()
            assert secret not in late_value
            assert cookie not in late_value
            assert "example.invalid" not in late_value
        finally:
            late_logger.removeHandler(late_handler)
            late_logger.propagate = True
    finally:
        root.removeHandler(handler)


def test_standard_provider_failure_is_redacted_and_never_retried(
    tmp_path: Path, monkeypatch
) -> None:
    output, _manifest_value, _applied = _install_execution_fakes(
        tmp_path, monkeypatch
    )
    provider = _FailOnceProvider()
    with pytest.raises(
        closure.LocalizationClosureError, match="provider_call_failed"
    ) as caught:
        closure.execute(
            output,
            primary_database="blombooru_sv1b_primary_test",
            replay_database="blombooru_sv1b_replay_test",
            provider=provider,
        )
    assert "sensitive-material" not in str(caught.value)
    checkpoint_path = output / "localization/localization-llm-checkpoint-private.json"
    assert "sensitive-material" not in checkpoint_path.read_text(encoding="utf-8")
    with pytest.raises(
        closure.LocalizationClosureError, match="provider_call_failed"
    ):
        closure.execute(
            output,
            primary_database="blombooru_sv1b_primary_test",
            replay_database="blombooru_sv1b_replay_test",
            provider=provider,
        )
    assert provider.standard_calls == 1


def test_ambiguous_item_uses_one_targeted_adjudication_not_batch_retry(
    tmp_path: Path, monkeypatch
) -> None:
    output, _manifest_value, _applied = _install_execution_fakes(
        tmp_path, monkeypatch
    )
    provider = _AmbiguousThenClearProvider()
    result = closure.execute(
        output,
        primary_database="blombooru_sv1b_primary_test",
        replay_database="blombooru_sv1b_replay_test",
        provider=provider,
    )
    state = json.loads(
        (output / "localization/localization-llm-checkpoint-private.json")
        .read_text(encoding="utf-8")
    )["batches"]["0001-test"]
    assert provider.standard_calls == 1
    assert provider.targeted_calls == 1
    assert provider.targeted_temperatures == [0]
    assert "strict item-level localization adjudication" in (
        provider.targeted_messages[0][0]["content"]
    )
    assert "at least one Chinese Han character" in (
        provider.targeted_messages[0][0]["content"]
    )
    assert state["attempt_count"] == 1
    assert state["item_adjudications"]["red_hat"]["attempt_count"] == 1
    assert result["localization_ambiguity_count"] == 0


def test_valid_batch_items_are_salvaged_and_only_invalid_item_is_adjudicated(
    tmp_path: Path, monkeypatch
) -> None:
    rows = [
        {"canonical_name": "red_hat", "category": "general"},
        {"canonical_name": "blue_sky", "category": "general"},
    ]
    output, _manifest_value, applied = _install_execution_fakes(
        tmp_path,
        monkeypatch,
        rows=rows,
        accepted_translation_count=2,
    )
    provider = _MixedProvider()
    result = closure.execute(
        output,
        primary_database="blombooru_sv1b_primary_test",
        replay_database="blombooru_sv1b_replay_test",
        provider=provider,
    )
    state = json.loads(
        (output / "localization/localization-llm-checkpoint-private.json")
        .read_text(encoding="utf-8")
    )["batches"]["0001-test"]
    assert result["accepted_new_translation_count"] == 2
    assert provider.standard_calls == 1
    assert provider.targeted_calls == 1
    assert set(applied[0][1]) == {"red_hat", "blue_sky"}
    assert state["item_validation"]["per_reason_counts"] == {
        "accepted_translation": 1,
        "untranslated_echo": 1,
    }
    sources = {
        row["canonical_name"]: row["source"]
        for row in state["terminal_item_outcomes"]
    }
    assert sources == {
        "blue_sky": "targeted_item_adjudication",
        "red_hat": "standard_batch",
    }


def test_private_raw_model_output_is_redacted_before_checkpoint_write(
    tmp_path: Path, monkeypatch
) -> None:
    output, _manifest_value, _applied = _install_execution_fakes(
        tmp_path, monkeypatch
    )
    provider = _RawSecretProjectionProvider()
    closure.execute(
        output,
        primary_database="blombooru_sv1b_primary_test",
        replay_database="blombooru_sv1b_replay_test",
        provider=provider,
    )
    checkpoint_text = (
        output / "localization/localization-llm-checkpoint-private.json"
    ).read_text(encoding="utf-8")
    assert "fake-raw-secret-123456789" not in checkpoint_text
    assert "example.invalid" not in checkpoint_text
    assert "[REDACTED" in checkpoint_text


def test_targeted_adjudication_failure_blocks_once_without_another_loop(
    tmp_path: Path, monkeypatch
) -> None:
    output, _manifest_value, _applied = _install_execution_fakes(
        tmp_path, monkeypatch
    )
    provider = _AlwaysAmbiguousProvider()
    with pytest.raises(
        closure.LocalizationClosureError,
        match="blocked_sv1b_localization_unresolved_item",
    ):
        closure.execute(
            output,
            primary_database="blombooru_sv1b_primary_test",
            replay_database="blombooru_sv1b_replay_test",
            provider=provider,
        )
    calls = provider.calls
    with pytest.raises(
        closure.LocalizationClosureError,
        match="blocked_sv1b_localization_unresolved_item",
    ):
        closure.execute(
            output,
            primary_database="blombooru_sv1b_primary_test",
            replay_database="blombooru_sv1b_replay_test",
            provider=provider,
        )
    assert provider.standard_calls == 1
    assert provider.targeted_calls == 1
    assert provider.calls == calls
    safe = json.loads(
        (output / "localization-unresolved-items-proof.json")
        .read_text(encoding="utf-8")
    )
    assert safe["unresolved_item_count"] == 1


def test_untranslated_echo_uses_targeted_call_without_full_batch_retry(
    tmp_path: Path, monkeypatch
) -> None:
    output, _manifest_value, _applied = _install_execution_fakes(
        tmp_path, monkeypatch
    )
    provider = _EchoThenClearProvider()
    result = closure.execute(
        output,
        primary_database="blombooru_sv1b_primary_test",
        replay_database="blombooru_sv1b_replay_test",
        provider=provider,
    )
    state = json.loads(
        (output / "localization/localization-llm-checkpoint-private.json")
        .read_text(encoding="utf-8")
    )["batches"]["0001-test"]
    assert result["localization_complete"] is True
    assert provider.standard_calls == 1
    assert provider.targeted_calls == 1
    assert state["attempt_count"] == 1
    assert state["status"] == "applied_both"


def test_display_preserve_policy_is_narrow_and_versioned() -> None:
    assert closure._display_preserve_outcome({
        "canonical_name": "2024", "category": "general"
    })["lexical_class"] == "pure_numeric_or_version_token"
    assert closure._display_preserve_outcome({
        "canonical_name": "NASA", "category": "general"
    })["lexical_class"] == "compact_uppercase_acronym"
    assert closure._display_preserve_outcome({
        "canonical_name": "4k", "category": "general"
    })["lexical_class"] == "durable_technical_token_allowlist"
    assert closure._display_preserve_outcome({
        "canonical_name": ":/", "category": "general"
    })["lexical_class"] == "standardized_ascii_symbol_token"
    assert closure._display_preserve_outcome({
        "canonical_name": "blue_sky", "category": "general"
    }) is None


def test_display_preserve_outcome_is_not_written_as_fake_translation(
    tmp_path: Path, monkeypatch
) -> None:
    rows = [{"canonical_name": "4k", "category": "general"}]
    output, _manifest_value, applied = _install_execution_fakes(
        tmp_path,
        monkeypatch,
        rows=rows,
        accepted_translation_count=0,
        blocking_missing=["4k"],
    )
    provider = _EchoThenClearProvider()
    result = closure.execute(
        output,
        primary_database="blombooru_sv1b_primary_test",
        replay_database="blombooru_sv1b_replay_test",
        provider=provider,
    )
    assert provider.standard_calls == 1
    assert provider.targeted_calls == 0
    assert applied == [
        ("blombooru_sv1b_primary_test", ()),
        ("blombooru_sv1b_replay_test", ()),
    ]
    assert result["accepted_new_translation_count"] == 0
    assert result["explicit_display_preserved_count"] == 1


def test_display_preserve_requires_echo_evidence_not_missing_result(
    tmp_path: Path, monkeypatch
) -> None:
    rows = [{"canonical_name": "4k", "category": "general"}]
    output, _manifest_value, _applied = _install_execution_fakes(
        tmp_path,
        monkeypatch,
        rows=rows,
        accepted_translation_count=1,
    )
    provider = _Provider()
    checkpoint_path = output / "localization/localization-llm-checkpoint-private.json"
    checkpoint_path.parent.mkdir(parents=True)
    checkpoint_path.write_text(json.dumps({
        "manifest_fingerprint": "f" * 64,
        "provider_route": "primary_only",
        "fallback_provider_used": False,
        "batches": {
            "0001-test": {
                "input_fingerprint": closure.sv1b.sha256_payload(rows),
                "attempt_count": 1,
                "original_batch_attempt_count": 1,
                "standard_batch_call_count": 1,
                "status": "item_adjudication_pending",
                "cost_upper_bound_usd": 0.01,
                "accepted_item_results": [],
                "unresolved_items": [{
                    "canonical_name": "4k",
                    "category": "general",
                    "verdict": "missing_result",
                    "result_fingerprint": None,
                }],
                "item_validation": {"unexpected_rows": []},
                "item_adjudications": {},
                "display_preserved_outcomes": [],
            }
        },
    }), encoding="utf-8")
    closure.execute(
        output,
        primary_database="blombooru_sv1b_primary_test",
        replay_database="blombooru_sv1b_replay_test",
        provider=provider,
    )
    assert provider.standard_calls == 0
    assert provider.targeted_calls == 1


def test_legacy_blocked_batch_migrates_without_reset_or_third_batch_call(
    tmp_path: Path, monkeypatch
) -> None:
    output, manifest, _applied = _install_execution_fakes(
        tmp_path, monkeypatch
    )
    checkpoint_path = output / "localization/localization-llm-checkpoint-private.json"
    checkpoint_path.parent.mkdir(parents=True)
    checkpoint_path.write_text(json.dumps({
        "manifest_fingerprint": "f" * 64,
        "provider_route": "primary_only",
        "fallback_provider_used": False,
        "batches": {
            "0001-test": {
                "input_fingerprint": manifest["batches"][0]["input_fingerprint"],
                "attempt_count": 2,
                "status": "blocked_localization_validation",
                "cost_upper_bound_usd": 0.02,
                "last_validation_reason": "localization_provider_untranslated_echo",
            }
        },
    }), encoding="utf-8")
    provider = _Provider()
    result = closure.execute(
        output,
        primary_database="blombooru_sv1b_primary_test",
        replay_database="blombooru_sv1b_replay_test",
        provider=provider,
    )
    state = json.loads(checkpoint_path.read_text(encoding="utf-8"))[
        "batches"
    ]["0001-test"]
    proof = json.loads(
        (output / "localization/blocked-batch-item-migration-proof-private.json")
        .read_text(encoding="utf-8")
    )
    assert result["localization_complete"] is True
    assert provider.standard_calls == 0
    assert provider.targeted_calls == 1
    assert state["attempt_count"] == 2
    assert state["original_batch_attempt_count"] == 2
    assert state["standard_batch_call_count"] == 2
    assert proof["legacy_response_not_persisted_count"] == 2
    assert proof["original_response_fingerprints"] == []
    assert proof["checkpoint_reset_performed"] is False
    assert proof["third_full_batch_call_authorized"] is False
