#!/usr/bin/env python3
"""Finite cache-first localization closure for the SCV2-SV1B test DB pair."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
for candidate in (ROOT, BACKEND):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from app.services.llm_translation_provider import FallbackProvider, get_llm_provider  # noqa: E402
from scripts import run_phase45_scv2_sv1b_controlled_pixiv_metadata_localization_source_graph_closure as sv1b  # noqa: E402


POLICY_VERSION = "sv1b_ai_tag_localization_policy_v1"
APPROVED_MODEL = "gpt-4.1-mini"
PRICING_POLICY_VERSION = "openai_gpt_4_1_mini_standard_20260719"
PRICING_SOURCE_URL = "https://developers.openai.com/api/docs/models/gpt-4.1-mini"
CHUNK_SIZE = 25
MAX_ATTEMPTS_PER_BATCH = 2
COST_CAP_USD = 10.0
# Official standard output-token rate is the higher of the model's input and
# output rates, so applying it to every token is a conservative upper bound.
COST_UPPER_BOUND_USD_PER_1K_TOKENS = 0.0016
# The approved provider path caps output at 4096 tokens. This 6800-token
# per-batch bound also reserves a conservative 2704 input/prompt tokens.
TOKENS_PER_BATCH_UPPER_BOUND = 6800
EXCLUDED_PROPER_NOUN_CATEGORIES = frozenset({"character", "copyright", "artist"})


class LocalizationClosureError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cost_upper_bound(tokens: int) -> float:
    return round((max(0, int(tokens)) / 1000.0) * COST_UPPER_BOUND_USD_PER_1K_TOKENS, 6)


def _category_by_tag(database: str, names: Iterable[str]) -> dict[str, str]:
    values = sorted({str(value) for value in names if value})
    if not values:
        return {}
    engine = sv1b.engine_for(database)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT name,CAST(category AS text) category FROM blombooru_tags WHERE name=ANY(:names)"),
                {"names": values},
            ).mappings()
            result = {str(row["name"]): str(row["category"]) for row in rows}
    finally:
        engine.dispose()
    if set(result) != set(values):
        raise LocalizationClosureError("localization_manifest_tag_membership_mismatch")
    return result


def build_manifest(output: Path, *, primary_database: str, replay_database: str) -> dict[str, Any]:
    output = output.resolve()
    sv1b.validate_owned_output_root(
        output, primary_database=primary_database, replay_database=replay_database
    )
    provider_preflight = sv1b.read_json(output / "provider-hardening-preflight.json")
    if provider_preflight.get("passed") is not True:
        raise LocalizationClosureError("localization_provider_hardening_gate_incomplete")
    acquisition = sv1b.read_json(output / "acquisition-closure-and-package-proof.json")
    if acquisition.get("passed") is not True:
        raise LocalizationClosureError("localization_acquisition_gate_incomplete")
    baseline = sv1b.read_json(output / "localization-baseline-proof.json")
    if baseline.get("accepted_translation_state", {}).get("fingerprint") is None:
        raise LocalizationClosureError("localization_baseline_binding_missing")
    private = sv1b.read_json(output / "localization-vocabulary-private.json")
    missing = sorted({str(value) for value in private.get("blocking_missing_ai_tags") or ()})
    categories = _category_by_tag(primary_database, missing)
    replay_categories = _category_by_tag(replay_database, missing)
    if categories != replay_categories:
        raise LocalizationClosureError("localization_primary_replay_category_mismatch")

    eligible = [
        {"canonical_name": name, "category": categories[name]}
        for name in missing
        if categories[name] not in EXCLUDED_PROPER_NOUN_CATEGORIES
    ]
    exclusions = [
        {
            "canonical_name": name,
            "category": categories[name],
            "reason_code": "ai_proper_noun_signal_not_identity_truth",
            "policy_version": POLICY_VERSION,
        }
        for name in missing
        if categories[name] in EXCLUDED_PROPER_NOUN_CATEGORIES
    ]
    batches = []
    for offset in range(0, len(eligible), CHUNK_SIZE):
        rows = eligible[offset:offset + CHUNK_SIZE]
        input_fingerprint = sv1b.sha256_payload(rows)
        batches.append({
            "batch_id": f"{offset // CHUNK_SIZE + 1:04d}-{input_fingerprint[:16]}",
            "input_fingerprint": input_fingerprint,
            "rows": rows,
        })
    projected_tokens_single_pass = len(batches) * TOKENS_PER_BATCH_UPPER_BOUND
    projected_tokens_including_retries = projected_tokens_single_pass * MAX_ATTEMPTS_PER_BATCH
    projected_cost = _cost_upper_bound(projected_tokens_including_retries)
    if projected_cost > COST_CAP_USD:
        raise LocalizationClosureError("projected_llm_cost_exceeds_usd10")

    manifest = {
        "policy_version": POLICY_VERSION,
        "approved_model": APPROVED_MODEL,
        "pricing_policy_version": PRICING_POLICY_VERSION,
        "pricing_source_url": PRICING_SOURCE_URL,
        "baseline_translation_fingerprint": baseline["accepted_translation_state"]["fingerprint"],
        "initial_missing_count": len(missing),
        "eligible_translation_count": len(eligible),
        "explicit_exclusion_count": len(exclusions),
        "category_counts": dict(sorted(Counter(categories.values()).items())),
        "batch_count": len(batches),
        "chunk_size": CHUNK_SIZE,
        "maximum_attempts_per_batch": MAX_ATTEMPTS_PER_BATCH,
        "projected_tokens_single_pass": projected_tokens_single_pass,
        "projected_tokens_including_retries": projected_tokens_including_retries,
        "projected_cost_upper_bound_usd": projected_cost,
        "cost_cap_usd": COST_CAP_USD,
        "eligible_rows": eligible,
        "explicit_exclusions": exclusions,
        "batches": batches,
    }
    manifest["manifest_fingerprint"] = sv1b.sha256_payload(manifest)
    localization_root = output / "localization"
    localization_root.mkdir(parents=True, exist_ok=True)
    manifest_path = localization_root / "localization-manifest-private.json"
    if manifest_path.is_file():
        existing = sv1b.read_json(manifest_path)
        if existing != manifest:
            raise LocalizationClosureError("localization_manifest_resume_binding_mismatch")
    else:
        sv1b.write_json(manifest_path, manifest)
    return manifest


def _validate_translation_rows(
    expected_rows: Iterable[Mapping[str, Any]], results: Iterable[Any]
) -> list[dict[str, Any]]:
    expected = {str(row["canonical_name"]): str(row["category"]) for row in expected_rows}
    normalized: dict[str, dict[str, Any]] = {}
    for result in results:
        canonical = str(getattr(result, "canonical_name", "") or "").strip()
        display = str(getattr(result, "display_name_zh", "") or "").strip()
        if not canonical or canonical not in expected or canonical in normalized:
            raise LocalizationClosureError("localization_provider_membership_invalid")
        if not display or len(display) > 500:
            raise LocalizationClosureError("localization_provider_display_invalid")
        if display.casefold().replace(" ", "_") == canonical.casefold():
            raise LocalizationClosureError("localization_provider_untranslated_echo")
        aliases = getattr(result, "aliases_zh", ()) or ()
        if not isinstance(aliases, (list, tuple)) or len(aliases) > 20:
            raise LocalizationClosureError("localization_provider_aliases_invalid")
        clean_aliases = []
        for alias in aliases:
            value = str(alias).strip()
            if value and value != display and len(value) <= 500:
                clean_aliases.append(value)
        normalized[canonical] = {
            "canonical_name": canonical,
            "display_name": display,
            "aliases": sorted(set(clean_aliases)),
            "category": expected[canonical],
            "needs_review": bool(getattr(result, "needs_review", False)),
        }
    if set(normalized) != set(expected):
        raise LocalizationClosureError("localization_provider_membership_invalid")
    return [normalized[name] for name in sorted(normalized)]


def _apply_batch(database: str, rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    values = [dict(row) for row in rows]
    names = [str(row["canonical_name"]) for row in values]
    engine = sv1b.engine_for(database)
    inserted = 0
    reused = 0
    try:
        with engine.begin() as connection:
            tag_ids = {
                str(row["name"]): int(row["id"])
                for row in connection.execute(
                    text("SELECT id,name FROM blombooru_tags WHERE name=ANY(:names)"),
                    {"names": names},
                ).mappings()
            }
            if set(tag_ids) != set(names):
                raise LocalizationClosureError("localization_target_tag_membership_mismatch")
            existing = {
                str(row["canonical_name"]): dict(row)
                for row in connection.execute(text("""
                    SELECT canonical_name,display_name,aliases_json,category,source,status,
                           needs_review,provider
                    FROM blombooru_tag_translations
                    WHERE language='zh-CN' AND canonical_name=ANY(:names)
                """), {"names": names}).mappings()
            }
            for row in values:
                name = str(row["canonical_name"])
                prior = existing.get(name)
                if prior is not None:
                    if (
                        str(prior.get("status")) not in {"translated", "reviewed"}
                        or not str(prior.get("display_name") or "").strip()
                    ):
                        raise LocalizationClosureError("localization_existing_row_not_accepted")
                    reused += 1
                    continue
                connection.execute(text("""
                    INSERT INTO blombooru_tag_translations
                        (tag_id,canonical_name,language,display_name,aliases_json,category,
                         source,status,confidence,needs_review,provider)
                    VALUES
                        (:tag_id,:canonical_name,'zh-CN',:display_name,:aliases_json,:category,
                         'llm','translated',NULL,:needs_review,'primary')
                """), {
                    "tag_id": tag_ids[name],
                    "canonical_name": name,
                    "display_name": str(row["display_name"]),
                    "aliases_json": json.dumps(row.get("aliases") or [], ensure_ascii=False),
                    "category": str(row["category"]),
                    "needs_review": bool(row.get("needs_review")),
                })
                inserted += 1
    finally:
        engine.dispose()
    return {"inserted": inserted, "reused": reused}


def _checkpoint_path(output: Path) -> Path:
    return output / "localization/localization-llm-checkpoint-private.json"


def _load_checkpoint(output: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    path = _checkpoint_path(output)
    if path.is_file():
        checkpoint = sv1b.read_json(path)
        if checkpoint.get("manifest_fingerprint") != manifest.get("manifest_fingerprint"):
            raise LocalizationClosureError("localization_checkpoint_manifest_mismatch")
        return checkpoint
    checkpoint = {
        "manifest_fingerprint": manifest["manifest_fingerprint"],
        "provider_route": "primary_only",
        "fallback_provider_used": False,
        "batches": {},
        "created_at": _utc_now(),
    }
    sv1b.write_json(path, checkpoint)
    return checkpoint


def execute(
    output: Path,
    *,
    primary_database: str,
    replay_database: str,
    provider: Any | None = None,
) -> dict[str, Any]:
    output = output.resolve()
    manifest = build_manifest(
        output, primary_database=primary_database, replay_database=replay_database
    )
    provider = provider or get_llm_provider()
    if isinstance(provider, FallbackProvider):
        raise LocalizationClosureError("localization_fallback_provider_forbidden")
    if manifest["eligible_translation_count"] and not provider.is_available():
        raise LocalizationClosureError("localization_primary_provider_unavailable")
    if manifest["eligible_translation_count"] and provider.get_provider_name() != "primary":
        raise LocalizationClosureError("localization_unapproved_provider_route")
    if manifest["eligible_translation_count"] and getattr(provider, "model", None) != APPROVED_MODEL:
        raise LocalizationClosureError("localization_unapproved_model")
    checkpoint = _load_checkpoint(output, manifest)

    for batch in manifest["batches"]:
        batch_id = str(batch["batch_id"])
        state = dict((checkpoint.get("batches") or {}).get(batch_id) or {})
        if state.get("status") in {"checkpointed", "applied_both"}:
            rows = state.get("translations") or []
            if sv1b.sha256_payload(rows) != state.get("translation_fingerprint"):
                raise LocalizationClosureError("localization_checkpoint_translation_drift")
        else:
            attempts = int(state.get("attempt_count") or 0)
            if attempts >= MAX_ATTEMPTS_PER_BATCH:
                raise LocalizationClosureError("localization_batch_retry_budget_exhausted")
            prior_cost_upper_bound = float(state.get("cost_upper_bound_usd") or 0.0)
            attempt_token_upper_bound = TOKENS_PER_BATCH_UPPER_BOUND
            reserved_cost_upper_bound = round(
                prior_cost_upper_bound + _cost_upper_bound(attempt_token_upper_bound), 6
            )
            other_cost_upper_bound = sum(
                float(value.get("cost_upper_bound_usd") or 0.0)
                for key, value in (checkpoint.get("batches") or {}).items()
                if key != batch_id
            )
            if round(other_cost_upper_bound + reserved_cost_upper_bound, 6) > COST_CAP_USD:
                raise LocalizationClosureError("llm_retry_would_exceed_usd10")
            state = {
                "input_fingerprint": batch["input_fingerprint"],
                "attempt_count": attempts + 1,
                "status": "in_flight",
                "cost_upper_bound_usd": reserved_cost_upper_bound,
                "started_at": _utc_now(),
            }
            checkpoint.setdefault("batches", {})[batch_id] = state
            sv1b.write_json(_checkpoint_path(output), checkpoint)
            inputs = [
                {"name": row["canonical_name"], "category": row["category"]}
                for row in batch["rows"]
            ]
            before_usage = dict(getattr(provider, "usage_totals", {}) or {})
            try:
                results = asyncio.run(provider.translate_tags(inputs))
            except Exception as exc:
                state["status"] = "failed_retryable"
                state["last_error_type"] = type(exc).__name__
                state["failed_at"] = _utc_now()
                checkpoint["batches"][batch_id] = state
                sv1b.write_json(_checkpoint_path(output), checkpoint)
                raise LocalizationClosureError(
                    "localization_primary_provider_call_failed"
                ) from None
            rows = _validate_translation_rows(batch["rows"], results)
            after_usage = dict(getattr(provider, "usage_totals", {}) or {})
            usage_tokens = max(
                0,
                int(after_usage.get("total_tokens", 0) or 0)
                - int(before_usage.get("total_tokens", 0) or 0),
            )
            if usage_tokens == 0:
                usage_tokens = attempt_token_upper_bound
            current_attempt_cost = _cost_upper_bound(usage_tokens)
            cumulative_cost_upper_bound = round(
                prior_cost_upper_bound + current_attempt_cost, 6
            )
            state.update({
                "status": "checkpointed",
                "translations": rows,
                "translation_fingerprint": sv1b.sha256_payload(rows),
                "usage_tokens_or_upper_bound": usage_tokens,
                "cost_upper_bound_usd": cumulative_cost_upper_bound,
                "finished_at": _utc_now(),
            })
            checkpoint["batches"][batch_id] = state
            sv1b.write_json(_checkpoint_path(output), checkpoint)

        primary_apply = _apply_batch(primary_database, rows)
        replay_apply = _apply_batch(replay_database, rows)
        state["primary_apply"] = primary_apply
        state["replay_apply"] = replay_apply
        state["status"] = "applied_both"
        checkpoint["batches"][batch_id] = state
        sv1b.write_json(_checkpoint_path(output), checkpoint)

    total_cost = round(sum(
        float(row.get("cost_upper_bound_usd") or 0.0)
        for row in checkpoint.get("batches", {}).values()
    ), 6)
    if total_cost > COST_CAP_USD:
        raise LocalizationClosureError("actual_llm_cost_upper_bound_exceeds_usd10")
    primary_vocabulary, primary_private = sv1b._vocabulary_state(primary_database)
    replay_vocabulary, replay_private = sv1b._vocabulary_state(replay_database)
    if primary_vocabulary != replay_vocabulary or primary_private != replay_private:
        raise LocalizationClosureError("localization_primary_replay_final_state_mismatch")
    excluded = {row["canonical_name"] for row in manifest["explicit_exclusions"]}
    remaining = set(primary_private["blocking_missing_ai_tags"])
    unexplained = sorted(remaining - excluded)
    if unexplained:
        raise LocalizationClosureError("localization_eligible_missing_after_execution")
    primary_state = sv1b._translation_logical_state(primary_database)
    replay_state = sv1b._translation_logical_state(replay_database)
    if primary_state != replay_state:
        raise LocalizationClosureError("localization_primary_replay_translation_mismatch")

    result = {
        "passed": True,
        "policy_version": POLICY_VERSION,
        "approved_model": APPROVED_MODEL,
        "pricing_policy_version": PRICING_POLICY_VERSION,
        "manifest_fingerprint": manifest["manifest_fingerprint"],
        "vocabulary": primary_vocabulary,
        "accepted_translation_state": primary_state,
        "initial_missing_ai_tag_count": manifest["initial_missing_count"],
        "accepted_new_translation_count": manifest["eligible_translation_count"],
        "explicit_nontranslatable_exclusion_count": len(excluded),
        "eligible_ai_tag_missing_count": len(unexplained),
        "silently_missing_eligible_count": 0,
        "provider_source_localized_count": 0,
        "creator_identity_translated_count": 0,
        "provider_tags_written_to_media_tags_count": 0,
        "original_provider_text_preserved": True,
        "external_llm_call_count": sum(
            int(row.get("attempt_count") or 0)
            for row in checkpoint.get("batches", {}).values()
        ),
        "projected_llm_cost_upper_bound_usd": manifest["projected_cost_upper_bound_usd"],
        "actual_llm_cost_upper_bound_usd": total_cost,
        "projected_and_actual_llm_cost_usd": max(
            float(manifest["projected_cost_upper_bound_usd"]), total_cost
        ),
        "fallback_provider_used": False,
        "image_upload_count": 0,
        "atomic_checkpoint_resume_used": True,
        "primary_replay_translation_fingerprint_equal": True,
        "localization_complete": True,
    }
    sv1b.write_json(output / "localization-closure-proof.json", result)
    return result
