from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import pytest

from app.services.llm_translation_provider import FallbackProvider, OpenAICompatibleProvider
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
        self.usage_totals = {"total_tokens": 0}
        self.model = closure.APPROVED_MODEL

    def is_available(self) -> bool:
        return True

    def get_provider_name(self) -> str:
        return "primary"

    async def translate_tags(self, rows):
        self.calls += 1
        self.usage_totals["total_tokens"] += 120
        return [
            _Translation(
                canonical_name=row["name"],
                display_name_zh=f"中文{index}",
                aliases_zh=[],
            )
            for index, row in enumerate(rows, 1)
        ]


class _FailOnceProvider(_Provider):
    async def translate_tags(self, rows):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("sensitive-material-must-not-reach-checkpoint")
        return await super().translate_tags(rows)


class _AmbiguousThenClearProvider(_Provider):
    async def translate_tags(self, rows):
        self.calls += 1
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
    async def translate_tags(self, rows):
        results = await super().translate_tags(rows)
        for result in results:
            result.needs_review = True
        return results


class _EchoThenClearProvider(_Provider):
    async def translate_tags(self, rows):
        self.calls += 1
        self.usage_totals["total_tokens"] += 120
        return [
            _Translation(
                canonical_name=row["name"],
                display_name_zh=(row["name"] if self.calls == 1 else "红帽"),
                aliases_zh=[],
            )
            for row in rows
        ]


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


def test_validate_translation_rows_requires_exact_non_echo_membership() -> None:
    expected = [{"canonical_name": "red_hat", "category": "general"}]
    rows = closure._validate_translation_rows(
        expected,
        [_Translation("red_hat", "红帽子", ["红色帽子"])],
    )
    assert rows == [{
        "canonical_name": "red_hat",
        "display_name": "红帽子",
        "aliases": ["红色帽子"],
        "category": "general",
        "needs_review": False,
    }]
    with pytest.raises(closure.LocalizationClosureError, match="untranslated_echo"):
        closure._validate_translation_rows(
            expected,
            [_Translation("red_hat", "red hat", [])],
        )
    with pytest.raises(closure.LocalizationClosureError, match="membership_invalid"):
        closure._validate_translation_rows(expected, [])


def test_execute_is_checkpoint_resumable_and_applies_both_databases(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "run"
    output.mkdir()
    row = {"canonical_name": "red_hat", "category": "general"}
    batch = {
        "batch_id": "0001-test",
        "input_fingerprint": closure.sv1b.sha256_payload([row]),
        "rows": [row],
    }
    manifest = {
        "manifest_fingerprint": "f" * 64,
        "initial_missing_count": 1,
        "eligible_translation_count": 1,
        "explicit_exclusions": [],
        "batches": [batch],
        "projected_cost_upper_bound_usd": 0.1,
    }
    monkeypatch.setattr(closure, "build_manifest", lambda *_args, **_kwargs: manifest)
    applied = []
    monkeypatch.setattr(
        closure,
        "_apply_batch",
        lambda database, rows: applied.append((database, tuple(r["canonical_name"] for r in rows))) or {"inserted": 1, "reused": 0},
    )
    vocabulary = {"blocking_missing_ai_translation_count": 0}
    private = {"blocking_missing_ai_tags": []}
    monkeypatch.setattr(closure.sv1b, "_vocabulary_state", lambda _database: (vocabulary, private))
    state = {"count": 1, "fingerprint": "s" * 64}
    monkeypatch.setattr(closure.sv1b, "_translation_logical_state", lambda _database: state)
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
    assert provider.calls == 1
    assert [database for database, _ in applied] == [
        "blombooru_sv1b_primary_test",
        "blombooru_sv1b_replay_test",
        "blombooru_sv1b_primary_test",
        "blombooru_sv1b_replay_test",
    ]
    checkpoint = json.loads(
        (output / "localization/localization-llm-checkpoint-private.json").read_text(encoding="utf-8")
    )
    assert checkpoint["batches"]["0001-test"]["status"] == "applied_both"


def test_execute_forbids_fallback_provider_before_any_call(tmp_path: Path, monkeypatch) -> None:
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
    with pytest.raises(closure.LocalizationClosureError, match="fallback_provider_forbidden"):
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


def test_execute_redacts_provider_failure_and_accounts_one_bounded_retry(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "run"
    output.mkdir()
    row = {"canonical_name": "red_hat", "category": "general"}
    manifest = {
        "manifest_fingerprint": "f" * 64,
        "initial_missing_count": 1,
        "eligible_translation_count": 1,
        "explicit_exclusions": [],
        "batches": [{
            "batch_id": "0001-test",
            "input_fingerprint": closure.sv1b.sha256_payload([row]),
            "rows": [row],
        }],
        "projected_cost_upper_bound_usd": 0.1,
    }
    monkeypatch.setattr(closure, "build_manifest", lambda *_args, **_kwargs: manifest)
    monkeypatch.setattr(closure, "_apply_batch", lambda *_args, **_kwargs: {"inserted": 1, "reused": 0})
    monkeypatch.setattr(
        closure.sv1b,
        "_vocabulary_state",
        lambda _database: ({"blocking_missing_ai_translation_count": 0}, {"blocking_missing_ai_tags": []}),
    )
    monkeypatch.setattr(
        closure.sv1b,
        "_translation_logical_state",
        lambda _database: {"count": 1, "fingerprint": "s" * 64},
    )
    provider = _FailOnceProvider()
    with pytest.raises(closure.LocalizationClosureError, match="provider_call_failed") as caught:
        closure.execute(
            output,
            primary_database="blombooru_sv1b_primary_test",
            replay_database="blombooru_sv1b_replay_test",
            provider=provider,
        )
    assert "sensitive-material" not in str(caught.value)
    checkpoint_path = output / "localization/localization-llm-checkpoint-private.json"
    failed_checkpoint = checkpoint_path.read_text(encoding="utf-8")
    assert "sensitive-material" not in failed_checkpoint

    result = closure.execute(
        output,
        primary_database="blombooru_sv1b_primary_test",
        replay_database="blombooru_sv1b_replay_test",
        provider=provider,
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    state = checkpoint["batches"]["0001-test"]
    assert result["localization_complete"] is True
    assert state["attempt_count"] == 2
    assert state["cost_upper_bound_usd"] > closure._cost_upper_bound(120)


def _install_single_batch_execution_fakes(tmp_path: Path, monkeypatch) -> Path:
    output = tmp_path / "run"
    output.mkdir()
    row = {"canonical_name": "red_hat", "category": "general"}
    monkeypatch.setattr(closure, "build_manifest", lambda *_args, **_kwargs: {
        "manifest_fingerprint": "f" * 64,
        "initial_missing_count": 1,
        "eligible_translation_count": 1,
        "explicit_exclusions": [],
        "batches": [{
            "batch_id": "0001-test",
            "input_fingerprint": closure.sv1b.sha256_payload([row]),
            "rows": [row],
        }],
        "projected_cost_upper_bound_usd": 0.1,
    })
    monkeypatch.setattr(closure, "_apply_batch", lambda *_args, **_kwargs: {"inserted": 1, "reused": 0})
    monkeypatch.setattr(
        closure.sv1b, "_vocabulary_state",
        lambda _database: ({"blocking_missing_ai_translation_count": 0}, {"blocking_missing_ai_tags": []}),
    )
    monkeypatch.setattr(
        closure.sv1b, "_translation_logical_state",
        lambda _database: {"count": 1, "fingerprint": "s" * 64},
    )
    return output


def test_execute_retries_ambiguous_translation_once_before_acceptance(tmp_path: Path, monkeypatch) -> None:
    output = _install_single_batch_execution_fakes(tmp_path, monkeypatch)
    provider = _AmbiguousThenClearProvider()
    result = closure.execute(
        output,
        primary_database="blombooru_sv1b_primary_test",
        replay_database="blombooru_sv1b_replay_test",
        provider=provider,
    )
    checkpoint = json.loads((output / "localization/localization-llm-checkpoint-private.json").read_text(encoding="utf-8"))
    state = checkpoint["batches"]["0001-test"]
    assert provider.calls == 2
    assert state["attempt_count"] == 2
    assert state["ambiguity_count"] == 0
    assert state["translations"][0]["needs_review"] is False
    assert result["localization_ambiguity_count"] == 0


def test_execute_blocks_when_clarification_remains_ambiguous(tmp_path: Path, monkeypatch) -> None:
    output = _install_single_batch_execution_fakes(tmp_path, monkeypatch)
    provider = _AlwaysAmbiguousProvider()
    with pytest.raises(closure.LocalizationClosureError, match="blocked_sv1b_localization_ambiguity"):
        closure.execute(
            output,
            primary_database="blombooru_sv1b_primary_test",
            replay_database="blombooru_sv1b_replay_test",
            provider=provider,
        )
    checkpoint = json.loads((output / "localization/localization-llm-checkpoint-private.json").read_text(encoding="utf-8"))
    state = checkpoint["batches"]["0001-test"]
    assert provider.calls == 2
    assert state["attempt_count"] == 2
    assert state["status"] == "blocked_localization_ambiguity"


def test_execute_retries_untranslated_echo_once_before_acceptance(tmp_path: Path, monkeypatch) -> None:
    output = _install_single_batch_execution_fakes(tmp_path, monkeypatch)
    provider = _EchoThenClearProvider()

    result = closure.execute(
        output,
        primary_database="blombooru_sv1b_primary_test",
        replay_database="blombooru_sv1b_replay_test",
        provider=provider,
    )

    checkpoint = json.loads((output / "localization/localization-llm-checkpoint-private.json").read_text(encoding="utf-8"))
    state = checkpoint["batches"]["0001-test"]
    assert result["localization_complete"] is True
    assert provider.calls == 2
    assert state["attempt_count"] == 2
    assert state["clarification_retry"] is True
    assert state["status"] == "applied_both"


def test_execute_recovers_unaccepted_inflight_attempt_without_resetting_budget(
    tmp_path: Path, monkeypatch
) -> None:
    output = _install_single_batch_execution_fakes(tmp_path, monkeypatch)
    checkpoint_path = output / "localization/localization-llm-checkpoint-private.json"
    checkpoint_path.parent.mkdir(parents=True)
    checkpoint_path.write_text(json.dumps({
        "manifest_fingerprint": "f" * 64,
        "provider_route": "primary_only",
        "fallback_provider_used": False,
        "batches": {
            "0001-test": {
                "input_fingerprint": closure.sv1b.sha256_payload([
                    {"canonical_name": "red_hat", "category": "general"}
                ]),
                "attempt_count": 1,
                "status": "in_flight",
                "cost_upper_bound_usd": closure._cost_upper_bound(
                    closure.TOKENS_PER_BATCH_UPPER_BOUND
                ),
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

    state = json.loads(checkpoint_path.read_text(encoding="utf-8"))["batches"]["0001-test"]
    assert result["localization_complete"] is True
    assert provider.calls == 1
    assert state["attempt_count"] == 2
    assert state["clarification_retry"] is True
    assert state["last_validation_reason"] == "localization_in_flight_result_not_accepted"
