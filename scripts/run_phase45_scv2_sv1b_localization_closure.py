#!/usr/bin/env python3
"""Finite cache-first localization closure for the SCV2-SV1B test DB pair."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
for candidate in (ROOT, BACKEND):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from app.services.llm_translation_provider import (  # noqa: E402
    FallbackProvider,
    TranslationResult,
    _loads_json_from_model_text,
    get_llm_provider,
    harden_llm_transport_logging,
    redact_llm_transport_log_text,
)
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
ITEM_VALIDATION_POLICY_VERSION = "sv1b_localization_item_validation_v1"
DISPLAY_PRESERVE_POLICY_VERSION = "sv1b_localization_display_preserve_v1"
CHECKPOINT_MIGRATION_VERSION = "sv1b_localization_blocked_batch_item_migration_v1"
TARGETED_ADJUDICATION_PROMPT_VERSION = "sv1b_localization_targeted_item_prompt_v1"
MANUAL_REVIEW_POLICY_VERSION = "sv1b_manual_localization_review_pending_v1"
MAX_STANDARD_CALLS_PER_NEW_BATCH = 1
MAX_ITEM_ADJUDICATION_ATTEMPTS = 1
MAX_MANUAL_REVIEW_PENDING_FOR_DOWNSTREAM = 8
TOKENS_PER_ITEM_ADJUDICATION_UPPER_BOUND = 1200
ITEM_VERDICTS = frozenset({
    "accepted_translation",
    "ambiguous_needs_review",
    "untranslated_echo",
    "invalid_display",
    "invalid_aliases",
    "missing_result",
    "unexpected_result",
    "duplicate_result",
})
TERMINAL_ITEM_OUTCOMES = frozenset({
    "accepted_translation",
    "explicit_display_preserved_nontranslatable",
    "manual_localization_review_pending",
    "manual_localization_override",
})
TECHNICAL_DISPLAY_PRESERVE_ALLOWLIST = frozenset({
    "2d", "3d", "4k", "8k", "ai", "ar", "cmyk", "css", "dna", "fps",
    "gif", "gps", "html", "jpeg", "json", "mp4", "png", "rgb", "usb",
    "vr", "webp", "wi-fi", "wifi", "xml",
})
_PURE_NUMERIC_OR_VERSION_RE = re.compile(
    r"^(?:v(?:ersion)?[_-]?)?\d+(?:[._-]\d+)*(?:[a-z])?$", re.IGNORECASE
)
_COMPACT_UPPERCASE_ACRONYM_RE = re.compile(r"^[A-Z][A-Z0-9+.-]{1,7}$")
_ASCII_SYMBOL_TOKEN_RE = re.compile(r"^[\x21-\x7e]{1,32}$")
_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


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


def _result_projection(result: Any) -> dict[str, Any]:
    aliases = getattr(result, "aliases_zh", None)
    return {
        "canonical_name": str(getattr(result, "canonical_name", "") or "").strip(),
        "display_name_zh": str(getattr(result, "display_name_zh", "") or "").strip(),
        "aliases_zh": aliases,
        "needs_review": bool(getattr(result, "needs_review", False)),
        "category": str(getattr(result, "category", "") or "").strip(),
    }


def _normalized_echo_value(value: str) -> str:
    return re.sub(r"[\s_-]+", "_", value.strip().casefold()).strip("_")


def _validate_translation_items(
    expected_rows: Iterable[Mapping[str, Any]],
    results: Iterable[Any],
    *,
    require_han: bool = False,
) -> dict[str, Any]:
    expected = {
        str(row["canonical_name"]): str(row["category"])
        for row in expected_rows
    }
    grouped: dict[str, list[dict[str, Any]]] = {}
    unexpected: list[dict[str, Any]] = []
    for result in results:
        projection = _result_projection(result)
        canonical = projection["canonical_name"]
        if canonical not in expected:
            unexpected.append({
                "canonical_name": canonical,
                "verdict": "unexpected_result",
                "result_fingerprint": sv1b.sha256_payload(projection),
            })
            continue
        grouped.setdefault(canonical, []).append(projection)

    accepted_rows: list[dict[str, Any]] = []
    unresolved_rows: list[dict[str, Any]] = []
    verdict_rows: list[dict[str, Any]] = []
    for canonical, category in sorted(expected.items()):
        values = grouped.get(canonical) or []
        if not values:
            verdict = "missing_result"
            projection = None
        elif len(values) > 1:
            verdict = "duplicate_result"
            projection = None
        else:
            projection = values[0]
            display = projection["display_name_zh"]
            aliases = projection["aliases_zh"]
            if not display or len(display) > 500:
                verdict = "invalid_display"
            elif (
                not isinstance(aliases, (list, tuple))
                or len(aliases) > 20
                or any(
                    not isinstance(alias, str)
                    or not alias.strip()
                    or len(alias.strip()) > 500
                    for alias in aliases
                )
            ):
                verdict = "invalid_aliases"
            elif _normalized_echo_value(display) == _normalized_echo_value(canonical):
                verdict = "untranslated_echo"
            elif require_han and not _HAN_RE.search(display):
                verdict = "invalid_display"
            elif projection["needs_review"]:
                verdict = "ambiguous_needs_review"
            else:
                verdict = "accepted_translation"

        verdict_row = {
            "canonical_name": canonical,
            "category": category,
            "verdict": verdict,
            "result_fingerprint": (
                sv1b.sha256_payload(projection) if projection is not None else None
            ),
        }
        verdict_rows.append(verdict_row)
        if verdict == "accepted_translation":
            assert projection is not None
            aliases = sorted({
                str(alias).strip()
                for alias in projection["aliases_zh"]
                if str(alias).strip() != projection["display_name_zh"]
            })
            accepted_rows.append({
                "canonical_name": canonical,
                "display_name": projection["display_name_zh"],
                "aliases": aliases,
                "category": category,
                "needs_review": False,
            })
        else:
            unresolved_rows.append(verdict_row)

    verdict_rows.extend(sorted(
        unexpected,
        key=lambda row: (str(row["canonical_name"]), str(row["result_fingerprint"])),
    ))
    reason_counts = Counter(str(row["verdict"]) for row in verdict_rows)
    if set(reason_counts) - ITEM_VERDICTS:
        raise LocalizationClosureError("localization_item_verdict_invalid")
    expected_membership = [
        {"canonical_name": name, "category": category}
        for name, category in sorted(expected.items())
    ]
    result_membership = sorted(
        str(row["canonical_name"])
        for values in grouped.values()
        for row in values
    ) + sorted(str(row["canonical_name"]) for row in unexpected)
    return {
        "policy_version": ITEM_VALIDATION_POLICY_VERSION,
        "accepted_rows": accepted_rows,
        "unresolved_rows": unresolved_rows,
        "unexpected_rows": unexpected,
        "verdict_rows": verdict_rows,
        "per_reason_counts": dict(sorted(reason_counts.items())),
        "expected_membership_fingerprint": sv1b.sha256_payload(expected_membership),
        "result_membership_fingerprint": sv1b.sha256_payload(result_membership),
        "verdict_membership_fingerprint": sv1b.sha256_payload(verdict_rows),
    }


def _display_preserve_outcome(row: Mapping[str, Any]) -> dict[str, Any] | None:
    canonical = str(row["canonical_name"])
    category = str(row["category"])
    lexical_class = None
    if canonical.casefold() in TECHNICAL_DISPLAY_PRESERVE_ALLOWLIST:
        lexical_class = "durable_technical_token_allowlist"
    elif _PURE_NUMERIC_OR_VERSION_RE.fullmatch(canonical):
        lexical_class = "pure_numeric_or_version_token"
    elif _COMPACT_UPPERCASE_ACRONYM_RE.fullmatch(canonical):
        lexical_class = "compact_uppercase_acronym"
    elif (
        _ASCII_SYMBOL_TOKEN_RE.fullmatch(canonical)
        and not any(character.isalpha() for character in canonical)
    ):
        lexical_class = "standardized_ascii_symbol_token"
    if lexical_class is None:
        return None
    return {
        "canonical_name": canonical,
        "category": category,
        "outcome": "explicit_display_preserved_nontranslatable",
        "lexical_class": lexical_class,
        "reason_code": "deterministic_display_preserve_token",
        "policy_version": DISPLAY_PRESERVE_POLICY_VERSION,
        "display_name": None,
        "search_display_fallback": "canonical_tag_search_and_display_fallback",
    }


def _manual_review_pending_outcome(
    row: Mapping[str, Any],
    *,
    batch_state: Mapping[str, Any],
    item_state: Mapping[str, Any],
) -> dict[str, Any]:
    canonical = str(row["canonical_name"])
    category = str(row["category"])
    validation = item_state.get("validation") or {}
    verdict_rows = [
        value for value in validation.get("verdict_rows") or ()
        if str(value.get("canonical_name")) == canonical
    ]
    verdict = str(
        (verdict_rows[0].get("verdict") if verdict_rows else None)
        or row.get("verdict")
        or item_state.get("status")
        or "unexplained"
    )
    targeted_status = str(item_state.get("status") or "attempt_not_completed")
    return {
        "canonical_name": canonical,
        "category": category,
        "outcome": "manual_localization_review_pending",
        "validator_verdict": verdict,
        "failure_reason": (
            f"targeted_adjudication_{verdict}_after_item_budget_exhaustion"
        ),
        "policy_version": MANUAL_REVIEW_POLICY_VERSION,
        "model_output": str(
            item_state.get("redacted_raw_model_output")
            or "model_output_not_safely_persisted"
        ),
        "call_attempt_history": {
            "original_batch_attempt_count": int(
                batch_state.get("original_batch_attempt_count")
                or batch_state.get("attempt_count")
                or 0
            ),
            "targeted_item_attempt_count": int(
                item_state.get("attempt_count") or 0
            ),
            "targeted_item_status": targeted_status,
            "targeted_prompt_version": item_state.get("prompt_version"),
        },
        "proposed_manual_review_question": (
            f"请确认 Danbooru {category} tag `{canonical}` 的 zh-CN 显示名称，"
            "或明确批准保持 canonical 显示。"
        ),
        "canonical_fallback_behavior": (
            "canonical_tag_search_and_display_fallback_without_chinese_completion_claim"
        ),
        "entity_or_truth_eligible": False,
    }


def _private_response_record(provider: Any, results: Iterable[Any]) -> dict[str, Any]:
    raw = str(getattr(provider, "last_completion_content", "") or "")
    if not raw:
        raw = json.dumps(
            [_result_projection(result) for result in results],
            ensure_ascii=False,
            sort_keys=True,
        )
    redacted = redact_llm_transport_log_text(raw)
    return {
        "raw_response_fingerprint": sv1b.sha256_payload(raw),
        "redacted_raw_model_output": redacted,
        "redacted_output_fingerprint": sv1b.sha256_payload(redacted),
        "raw_output_redacted": True,
        "request_response_headers_persisted": False,
    }


def _usage_tokens(provider: Any, before_usage: Mapping[str, Any], fallback: int) -> int:
    after_usage = dict(getattr(provider, "usage_totals", {}) or {})
    return max(
        0,
        int(after_usage.get("total_tokens", 0) or 0)
        - int(before_usage.get("total_tokens", 0) or 0),
    ) or fallback


def _targeted_messages(row: Mapping[str, Any]) -> list[dict[str, str]]:
    system_prompt = (
        "You are performing one strict item-level localization adjudication. "
        "The item is a Danbooru general tag requiring a zh-CN display localization. "
        "Do not repeat the canonical English name as the display label. "
        "For an ordinary translatable general tag, display_name_zh must contain at "
        "least one Chinese Han character. Preserve the exact semantic meaning. "
        "Return exact JSON membership for this one item. Set needs_review=false only "
        "when definitive. Do not add markdown, prose, or unrelated explanation. "
        "Return one JSON object with canonical_name, display_name_zh, aliases_zh, "
        "and needs_review."
    )
    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": json.dumps({
                "canonical_name": str(row["canonical_name"]),
                "category": str(row["category"]),
                "prompt_version": TARGETED_ADJUDICATION_PROMPT_VERSION,
            }, ensure_ascii=False, sort_keys=True),
        },
    ]


async def _targeted_adjudication(provider: Any, row: Mapping[str, Any]) -> tuple[list[Any], str]:
    content = await provider.complete_chat(
        _targeted_messages(row),
        temperature=0,
        max_tokens=512,
    )
    parsed = _loads_json_from_model_text(content)
    if isinstance(parsed, list):
        values = parsed
    else:
        values = [parsed]
    results = []
    for value in values:
        if not isinstance(value, dict):
            continue
        results.append(TranslationResult(
            canonical_name=str(value.get("canonical_name") or ""),
            display_name_zh=str(value.get("display_name_zh") or ""),
            aliases_zh=value.get("aliases_zh") or [],
            needs_review=bool(value.get("needs_review", True)),
            category=str(row["category"]),
        ))
    return results, content


def _apply_batch(database: str, rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    values = [dict(row) for row in rows]
    if any(bool(row.get("needs_review")) for row in values):
        raise LocalizationClosureError("blocked_sv1b_localization_ambiguity")
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
                    prior_aliases = prior.get("aliases_json") or []
                    if isinstance(prior_aliases, str):
                        prior_aliases = json.loads(prior_aliases)
                    if (
                        str(prior.get("status")) not in {"translated", "reviewed"}
                        or not str(prior.get("display_name") or "").strip()
                        or bool(prior.get("needs_review"))
                        or str(prior.get("display_name")) != str(row["display_name"])
                        or sorted(prior_aliases) != sorted(row.get("aliases") or [])
                        or str(prior.get("category")) != str(row["category"])
                        or str(prior.get("provider")) != "primary"
                    ):
                        raise LocalizationClosureError(
                            "localization_existing_row_payload_mismatch"
                        )
                    reused += 1
                    continue
                connection.execute(text("""
                    INSERT INTO blombooru_tag_translations
                        (tag_id,canonical_name,language,display_name,aliases_json,category,
                         source,status,confidence,needs_review,provider)
                    VALUES
                        (:tag_id,:canonical_name,'zh-CN',:display_name,:aliases_json,:category,
                         'llm','translated',NULL,false,'primary')
                """), {
                    "tag_id": tag_ids[name],
                    "canonical_name": name,
                    "display_name": str(row["display_name"]),
                    "aliases_json": json.dumps(row.get("aliases") or [], ensure_ascii=False),
                    "category": str(row["category"]),
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


def _is_approved_primary_provider_route(provider: Any) -> bool:
    """Accept the real primary provider identity without admitting fallback routes."""

    provider_name = str(provider.get_provider_name())
    provider_label = getattr(provider, "label", None)
    if provider_name == "primary":
        return provider_label in {None, "primary"}
    return provider_name == "openai_compatible(primary)" and provider_label == "primary"


def _checkpoint_cost_upper_bound(checkpoint: Mapping[str, Any]) -> float:
    return round(sum(
        float(row.get("cost_upper_bound_usd") or 0.0)
        for row in (checkpoint.get("batches") or {}).values()
    ), 6)


def _write_checkpoint(output: Path, checkpoint: Mapping[str, Any]) -> None:
    sv1b.write_json(_checkpoint_path(output), checkpoint)


def _checkpoint_resume_metrics(checkpoint: Mapping[str, Any]) -> dict[str, int]:
    applied_translations = 0
    targeted_accepted = 0
    standard_calls = 0
    targeted_calls = 0
    manual_pending = 0
    for state in (checkpoint.get("batches") or {}).values():
        standard_calls += int(
            state.get("standard_batch_call_count")
            or state.get("attempt_count")
            or 0
        )
        if state.get("status") == "applied_both":
            applied_translations += len(state.get("translations") or ())
        manual_pending += len(state.get("manual_review_pending_outcomes") or ())
        for item in (state.get("item_adjudications") or {}).values():
            targeted_calls += int(item.get("attempt_count") or 0)
            targeted_accepted += int(
                item.get("status") == "accepted_translation"
            )
    return {
        "applied_translation_count": applied_translations,
        "targeted_accepted_result_count": targeted_accepted,
        "manual_review_pending_count": manual_pending,
        "standard_call_count": standard_calls,
        "targeted_call_count": targeted_calls,
        "avoided_duplicate_call_count": standard_calls + targeted_calls,
    }


def _write_disposition_ledgers(
    output: Path,
    *,
    manifest: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
) -> dict[str, Any]:
    dispositions: dict[str, dict[str, Any]] = {}
    for batch in manifest["batches"]:
        state = dict((checkpoint.get("batches") or {}).get(str(batch["batch_id"])) or {})
        outcomes = list(state.get("terminal_item_outcomes") or ())
        if not outcomes:
            outcomes.extend({
                "canonical_name": str(row["canonical_name"]),
                "category": str(row["category"]),
                "outcome": "accepted_translation",
                "source": "accepted_batch_checkpoint",
            } for row in state.get("translations") or ())
            outcomes.extend(state.get("display_preserved_outcomes") or ())
            outcomes.extend(state.get("manual_review_pending_outcomes") or ())
            outcomes.extend(
                {
                    "canonical_name": str(item["translation"]["canonical_name"]),
                    "category": str(item["translation"]["category"]),
                    "outcome": "accepted_translation",
                    "source": "targeted_item_adjudication",
                }
                for item in (state.get("item_adjudications") or {}).values()
                if item.get("status") == "accepted_translation"
                and isinstance(item.get("translation"), Mapping)
            )
        for outcome in outcomes:
            canonical = str(outcome["canonical_name"])
            value = dict(outcome)
            previous = dispositions.get(canonical)
            if previous is not None and previous != value:
                raise LocalizationClosureError(
                    "localization_duplicate_or_conflicting_disposition"
                )
            dispositions[canonical] = value

    rows = [dispositions[key] for key in sorted(dispositions)]
    pending = [
        row for row in rows
        if row.get("outcome") == "manual_localization_review_pending"
    ]
    private = {
        "policy_version": MANUAL_REVIEW_POLICY_VERSION,
        "manifest_fingerprint": manifest["manifest_fingerprint"],
        "item_dispositions": rows,
        "disposition_count": len(rows),
        "disposition_membership_fingerprint": sv1b.sha256_payload(rows),
    }
    public = {
        "policy_version": MANUAL_REVIEW_POLICY_VERSION,
        "manual_localization_review_pending": pending,
        "manual_review_pending_count": len(pending),
        "manual_review_pending_membership_fingerprint": sv1b.sha256_payload(
            pending
        ),
        "canonical_content_redacted": False,
        "security_sensitive_material_redacted": True,
    }
    sv1b.write_json(
        output / "localization/localization-item-disposition-ledger-private.json",
        private,
    )
    sv1b.write_json(
        output / "localization/localization-manual-review-pending.json",
        public,
    )
    return {
        "known_disposition_count": len(rows),
        "manual_review_pending_count": len(pending),
        "disposition_membership_fingerprint": private[
            "disposition_membership_fingerprint"
        ],
        "manual_review_pending_membership_fingerprint": public[
            "manual_review_pending_membership_fingerprint"
        ],
    }


def _migrate_legacy_blocked_batch(
    output: Path,
    *,
    batch: Mapping[str, Any],
    state: Mapping[str, Any],
    checkpoint: dict[str, Any],
) -> dict[str, Any]:
    batch_id = str(batch["batch_id"])
    if state.get("checkpoint_migration_version") == CHECKPOINT_MIGRATION_VERSION:
        return dict(state)
    if (
        state.get("status") != "blocked_localization_validation"
        or int(state.get("attempt_count") or 0) != 2
        or state.get("input_fingerprint") != batch.get("input_fingerprint")
    ):
        raise LocalizationClosureError("localization_legacy_blocked_batch_not_migratable")
    legacy_reason = str(
        state.get("last_validation_reason")
        or "legacy_validation_reason_not_persisted"
    )
    unresolved = [
        {
            "canonical_name": str(row["canonical_name"]),
            "category": str(row["category"]),
            "verdict": "missing_result",
            "result_fingerprint": None,
        }
        for row in batch["rows"]
    ]
    migrated = {
        **dict(state),
        "status": "item_adjudication_pending",
        "checkpoint_migration_version": CHECKPOINT_MIGRATION_VERSION,
        "original_batch_attempt_count": 2,
        "standard_batch_call_count": 2,
        "prior_validation_reasons": [
            {
                "attempt_number": 1,
                "safe_reason": "legacy_attempt_response_not_persisted",
            },
            {
                "attempt_number": 2,
                "safe_reason": legacy_reason,
            },
        ],
        "legacy_response_slots": [
            {
                "attempt_number": 1,
                "response_persisted": False,
                "response_fingerprint": None,
            },
            {
                "attempt_number": 2,
                "response_persisted": False,
                "response_fingerprint": None,
            },
        ],
        "legacy_response_not_persisted_count": 2,
        "original_response_fingerprints": [],
        "accepted_item_results": [],
        "unresolved_items": unresolved,
        "display_preserved_outcomes": [],
        "item_adjudications": {},
        "item_validation": {
            "policy_version": ITEM_VALIDATION_POLICY_VERSION,
            "per_reason_counts": {"missing_result": len(unresolved)},
            "expected_membership_fingerprint": sv1b.sha256_payload(list(batch["rows"])),
            "verdict_membership_fingerprint": sv1b.sha256_payload(unresolved),
            "legacy_raw_response_revalidation_possible": False,
        },
    }
    checkpoint.setdefault("batches", {})[batch_id] = migrated
    _write_checkpoint(output, checkpoint)
    proof = {
        "migration_version": CHECKPOINT_MIGRATION_VERSION,
        "batch_id": batch_id,
        "input_fingerprint": batch["input_fingerprint"],
        "original_batch_attempt_count": 2,
        "migrated_batch_attempt_count": int(migrated["attempt_count"]),
        "cost_upper_bound_usd": float(migrated.get("cost_upper_bound_usd") or 0.0),
        "prior_validation_reasons": migrated["prior_validation_reasons"],
        "original_response_fingerprints": [],
        "legacy_response_not_persisted_count": 2,
        "accepted_existing_item_count": 0,
        "unresolved_item_count": len(unresolved),
        "unresolved_items": unresolved,
        "third_full_batch_call_authorized": False,
        "checkpoint_reset_performed": False,
    }
    proof["migration_fingerprint"] = sv1b.sha256_payload(proof)
    proof_path = output / "localization/blocked-batch-item-migration-proof-private.json"
    if proof_path.is_file():
        if sv1b.read_json(proof_path) != proof:
            raise LocalizationClosureError("localization_checkpoint_migration_proof_drift")
    else:
        sv1b.write_json(proof_path, proof)
    return migrated


def _write_unresolved_item_block(
    output: Path,
    *,
    batch_id: str,
    rows: Iterable[Mapping[str, Any]],
) -> None:
    exact = sorted(
        [dict(row) for row in rows],
        key=lambda row: str(row.get("canonical_name")),
    )
    private = {
        "status": "blocked_sv1b_localization_provider_call",
        "batch_private_ref": sv1b.sha256_payload(batch_id)[:16],
        "unresolved_items": exact,
        "unresolved_membership_fingerprint": sv1b.sha256_payload(exact),
    }
    safe = {
        "status": private["status"],
        "batch_private_ref": private["batch_private_ref"],
        "unresolved_item_count": len(exact),
        "unresolved_reason_counts": dict(sorted(Counter(
            str(row.get("verdict") or row.get("status") or "unexplained")
            for row in exact
        ).items())),
        "unresolved_membership_fingerprint": private[
            "unresolved_membership_fingerprint"
        ],
    }
    sv1b.write_json(output / "localization-unresolved-items-private.json", private)
    sv1b.write_json(output / "localization-unresolved-items-proof.json", safe)


def _prepare_standard_batch(
    output: Path,
    *,
    batch: Mapping[str, Any],
    state: Mapping[str, Any],
    checkpoint: dict[str, Any],
    provider: Any,
) -> dict[str, Any]:
    batch_id = str(batch["batch_id"])
    if state:
        return dict(state)
    prior_cost = 0.0
    reserved = _cost_upper_bound(TOKENS_PER_BATCH_UPPER_BOUND)
    if round(_checkpoint_cost_upper_bound(checkpoint) + reserved, 6) > COST_CAP_USD:
        raise LocalizationClosureError("llm_standard_batch_would_exceed_usd10")
    current = {
        "input_fingerprint": batch["input_fingerprint"],
        "attempt_count": 1,
        "original_batch_attempt_count": 1,
        "standard_batch_call_count": MAX_STANDARD_CALLS_PER_NEW_BATCH,
        "status": "standard_batch_in_flight",
        "cost_upper_bound_usd": reserved,
        "started_at": _utc_now(),
        "item_adjudications": {},
        "display_preserved_outcomes": [],
    }
    checkpoint.setdefault("batches", {})[batch_id] = current
    _write_checkpoint(output, checkpoint)
    inputs = [
        {"name": row["canonical_name"], "category": row["category"]}
        for row in batch["rows"]
    ]
    before_usage = dict(getattr(provider, "usage_totals", {}) or {})
    try:
        results = asyncio.run(provider.translate_tags(inputs))
    except Exception as exc:
        current.update({
            "status": "blocked_standard_batch_provider_call",
            "last_error_type": type(exc).__name__,
            "failed_at": _utc_now(),
        })
        checkpoint["batches"][batch_id] = current
        _write_checkpoint(output, checkpoint)
        raise LocalizationClosureError(
            "localization_primary_provider_call_failed"
        ) from None
    usage_tokens = _usage_tokens(
        provider, before_usage, TOKENS_PER_BATCH_UPPER_BOUND
    )
    validation = _validate_translation_items(
        batch["rows"], results, require_han=True
    )
    current.update({
        "status": "item_adjudication_pending",
        "cost_upper_bound_usd": round(
            prior_cost + _cost_upper_bound(usage_tokens), 6
        ),
        "usage_tokens_or_upper_bound": usage_tokens,
        "accepted_item_results": validation["accepted_rows"],
        "unresolved_items": validation["unresolved_rows"],
        "item_validation": {
            key: value
            for key, value in validation.items()
            if key not in {"accepted_rows", "unresolved_rows"}
        },
        "standard_response_history": [
            {
                "attempt_number": 1,
                **_private_response_record(provider, results),
            }
        ],
        "finished_at": _utc_now(),
    })
    checkpoint["batches"][batch_id] = current
    _write_checkpoint(output, checkpoint)
    return current


def _resolve_batch_items(
    output: Path,
    *,
    batch: Mapping[str, Any],
    state: Mapping[str, Any],
    checkpoint: dict[str, Any],
    provider: Any,
) -> dict[str, Any]:
    batch_id = str(batch["batch_id"])
    current = dict(state)
    accepted_by_name = {
        str(row["canonical_name"]): dict(row)
        for row in current.get("accepted_item_results") or ()
    }
    display_by_name = {
        str(row["canonical_name"]): dict(row)
        for row in current.get("display_preserved_outcomes") or ()
    }
    manual_by_name = {
        str(row["canonical_name"]): dict(row)
        for row in current.get("manual_review_pending_outcomes") or ()
    }
    item_adjudications = {
        str(key): dict(value)
        for key, value in (current.get("item_adjudications") or {}).items()
    }
    unexpected = list(
        (current.get("item_validation") or {}).get("unexpected_rows") or ()
    )
    current["unexpected_response_diagnostics"] = unexpected
    unresolved = list(current.get("unresolved_items") or ())
    for unresolved_row in unresolved:
        canonical = str(unresolved_row["canonical_name"])
        if (
            canonical in accepted_by_name
            or canonical in display_by_name
            or canonical in manual_by_name
        ):
            continue
        preserve = (
            _display_preserve_outcome(unresolved_row)
            if unresolved_row.get("verdict") == "untranslated_echo"
            else None
        )
        if preserve is not None:
            display_by_name[canonical] = preserve
            continue
        item_state = dict(item_adjudications.get(canonical) or {})
        if item_state.get("status") == "accepted_translation":
            accepted_by_name[canonical] = dict(item_state["translation"])
            continue
        if item_state.get("status") == "explicit_display_preserved_nontranslatable":
            display_by_name[canonical] = dict(
                item_state["display_preserved_outcome"]
            )
            continue
        if item_state.get("status") == "manual_localization_review_pending":
            manual_by_name[canonical] = dict(
                item_state["manual_review_pending_outcome"]
            )
            continue
        if int(item_state.get("attempt_count") or 0) >= MAX_ITEM_ADJUDICATION_ATTEMPTS:
            if item_state.get("status") == "blocked_provider_call":
                raise LocalizationClosureError(
                    "localization_primary_provider_call_failed"
                )
            pending = _manual_review_pending_outcome(
                unresolved_row,
                batch_state=current,
                item_state=item_state,
            )
            item_state["status"] = "manual_localization_review_pending"
            item_state["manual_localization_review_pending_at"] = _utc_now()
            item_state["manual_review_pending_outcome"] = pending
            item_state["manual_review_pending_fingerprint"] = sv1b.sha256_payload(
                pending
            )
            manual_by_name[canonical] = pending
            item_adjudications[canonical] = item_state
            current["item_adjudications"] = item_adjudications
            current["manual_review_pending_outcomes"] = [
                manual_by_name[name] for name in sorted(manual_by_name)
            ]
            checkpoint["batches"][batch_id] = current
            _write_checkpoint(output, checkpoint)
            continue
        reserved = _cost_upper_bound(TOKENS_PER_ITEM_ADJUDICATION_UPPER_BOUND)
        if round(_checkpoint_cost_upper_bound(checkpoint) + reserved, 6) > COST_CAP_USD:
            raise LocalizationClosureError("llm_item_adjudication_would_exceed_usd10")
        prior_cost = float(current.get("cost_upper_bound_usd") or 0.0)
        item_state = {
            "attempt_count": 1,
            "status": "in_flight",
            "prompt_version": TARGETED_ADJUDICATION_PROMPT_VERSION,
            "temperature": 0,
            "cost_upper_bound_usd": reserved,
            "started_at": _utc_now(),
        }
        item_adjudications[canonical] = item_state
        current["item_adjudications"] = item_adjudications
        current["cost_upper_bound_usd"] = round(prior_cost + reserved, 6)
        checkpoint["batches"][batch_id] = current
        _write_checkpoint(output, checkpoint)
        before_usage = dict(getattr(provider, "usage_totals", {}) or {})
        try:
            results, raw_content = asyncio.run(
                _targeted_adjudication(provider, unresolved_row)
            )
        except Exception as exc:
            item_state.update({
                "status": "blocked_provider_call",
                "last_error_type": type(exc).__name__,
                "failed_at": _utc_now(),
            })
            item_adjudications[canonical] = item_state
            current["item_adjudications"] = item_adjudications
            checkpoint["batches"][batch_id] = current
            _write_checkpoint(output, checkpoint)
            _write_unresolved_item_block(
                output,
                batch_id=batch_id,
                rows=[{**dict(unresolved_row), "status": "blocked_provider_call"}],
            )
            raise LocalizationClosureError(
                "blocked_sv1b_localization_provider_call"
            ) from None
        usage_tokens = _usage_tokens(
            provider, before_usage, TOKENS_PER_ITEM_ADJUDICATION_UPPER_BOUND
        )
        validation = _validate_translation_items(
            [unresolved_row], results, require_han=True
        )
        targeted_preserve = (
            _display_preserve_outcome(unresolved_row)
            if (
                len(validation["unresolved_rows"]) == 1
                and validation["unresolved_rows"][0].get("verdict")
                == "untranslated_echo"
                and not validation["unexpected_rows"]
            )
            else None
        )
        redacted_content = redact_llm_transport_log_text(raw_content)
        item_state.update({
            "status": (
                "accepted_translation"
                if len(validation["accepted_rows"]) == 1
                and not validation["unresolved_rows"]
                and not validation["unexpected_rows"]
                else (
                    "explicit_display_preserved_nontranslatable"
                    if targeted_preserve is not None
                    else "blocked_invalid_result"
                )
            ),
            "usage_tokens_or_upper_bound": usage_tokens,
            "cost_upper_bound_usd": _cost_upper_bound(usage_tokens),
            "validation": {
                key: value
                for key, value in validation.items()
                if key not in {"accepted_rows", "unresolved_rows"}
            },
            "raw_response_fingerprint": sv1b.sha256_payload(raw_content),
            "redacted_raw_model_output": redacted_content,
            "redacted_output_fingerprint": sv1b.sha256_payload(redacted_content),
            "raw_output_redacted": True,
            "finished_at": _utc_now(),
        })
        current["cost_upper_bound_usd"] = round(
            prior_cost + _cost_upper_bound(usage_tokens), 6
        )
        if item_state["status"] == "accepted_translation":
            translation = dict(validation["accepted_rows"][0])
            item_state["translation"] = translation
            item_state["translation_fingerprint"] = sv1b.sha256_payload(
                translation
            )
            accepted_by_name[canonical] = translation
        elif targeted_preserve is not None:
            item_state["display_preserved_outcome"] = targeted_preserve
            item_state["display_preserved_fingerprint"] = sv1b.sha256_payload(
                targeted_preserve
            )
            display_by_name[canonical] = targeted_preserve
        else:
            pending_reasons = (
                validation["unresolved_rows"]
                or validation["unexpected_rows"]
                or [{
                    **dict(unresolved_row),
                    "verdict": "unexplained",
                }]
            )
            pending_row = dict(pending_reasons[0])
            pending = _manual_review_pending_outcome(
                pending_row,
                batch_state=current,
                item_state=item_state,
            )
            item_state["status"] = "manual_localization_review_pending"
            item_state["manual_localization_review_pending_at"] = _utc_now()
            item_state["manual_review_pending_outcome"] = pending
            item_state["manual_review_pending_fingerprint"] = sv1b.sha256_payload(
                pending
            )
            manual_by_name[canonical] = pending
        item_adjudications[canonical] = item_state
        current["item_adjudications"] = item_adjudications
        current["manual_review_pending_outcomes"] = [
            manual_by_name[name] for name in sorted(manual_by_name)
        ]
        checkpoint["batches"][batch_id] = current
        _write_checkpoint(output, checkpoint)

    expected_names = {
        str(row["canonical_name"]) for row in batch["rows"]
    }
    accepted_names = set(accepted_by_name)
    display_names = set(display_by_name)
    manual_names = set(manual_by_name)
    if (
        accepted_names.intersection(display_names)
        or accepted_names.intersection(manual_names)
        or display_names.intersection(manual_names)
        or accepted_names.union(display_names).union(manual_names)
        != expected_names
    ):
        raise LocalizationClosureError("localization_terminal_item_membership_invalid")
    translations = [accepted_by_name[name] for name in sorted(accepted_names)]
    display_preserved = [display_by_name[name] for name in sorted(display_names)]
    manual_pending = [manual_by_name[name] for name in sorted(manual_names)]
    terminal_outcomes = sorted([
        *[
            {
                "canonical_name": name,
                "category": str(accepted_by_name[name]["category"]),
                "outcome": "accepted_translation",
                "source": (
                    "targeted_item_adjudication"
                    if name in item_adjudications
                    else "standard_batch"
                ),
            }
            for name in accepted_names
        ],
        *display_preserved,
        *manual_pending,
    ], key=lambda row: str(row["canonical_name"]))
    current.update({
        "status": "checkpointed",
        "accepted_item_results": translations,
        "unresolved_items": [],
        "display_preserved_outcomes": display_preserved,
        "manual_review_pending_outcomes": manual_pending,
        "item_adjudications": item_adjudications,
        "translations": translations,
        "translation_fingerprint": sv1b.sha256_payload(translations),
        "terminal_item_outcomes": terminal_outcomes,
        "terminal_item_membership_fingerprint": sv1b.sha256_payload(
            terminal_outcomes
        ),
        "finished_at": _utc_now(),
    })
    checkpoint["batches"][batch_id] = current
    _write_checkpoint(output, checkpoint)
    return current


def execute(
    output: Path,
    *,
    primary_database: str,
    replay_database: str,
    provider: Any | None = None,
) -> dict[str, Any]:
    output = output.resolve()
    transport_logging = harden_llm_transport_logging()
    manifest = build_manifest(
        output, primary_database=primary_database, replay_database=replay_database
    )
    provider = provider or get_llm_provider()
    if isinstance(provider, FallbackProvider):
        raise LocalizationClosureError("localization_fallback_provider_forbidden")
    if manifest["eligible_translation_count"] and not provider.is_available():
        raise LocalizationClosureError("localization_primary_provider_unavailable")
    if manifest["eligible_translation_count"] and not _is_approved_primary_provider_route(provider):
        raise LocalizationClosureError("localization_unapproved_provider_route")
    if manifest["eligible_translation_count"] and getattr(provider, "model", None) != APPROVED_MODEL:
        raise LocalizationClosureError("localization_unapproved_model")
    checkpoint = _load_checkpoint(output, manifest)
    resume_metrics = _checkpoint_resume_metrics(checkpoint)

    for batch in manifest["batches"]:
        batch_id = str(batch["batch_id"])
        state = dict((checkpoint.get("batches") or {}).get(batch_id) or {})
        if state.get("status") == "blocked_localization_validation":
            state = _migrate_legacy_blocked_batch(
                output,
                batch=batch,
                state=state,
                checkpoint=checkpoint,
            )
        if state.get("status") == "blocked_standard_batch_provider_call":
            raise LocalizationClosureError(
                "localization_primary_provider_call_failed"
            )
        if state.get("status") == "applied_both":
            rows = state.get("translations") or []
            if sv1b.sha256_payload(rows) != state.get("translation_fingerprint"):
                raise LocalizationClosureError("localization_checkpoint_translation_drift")
            continue
        state = _prepare_standard_batch(
            output,
            batch=batch,
            state=state,
            checkpoint=checkpoint,
            provider=provider,
        )
        if state.get("status") != "checkpointed":
            state = _resolve_batch_items(
                output,
                batch=batch,
                state=state,
                checkpoint=checkpoint,
                provider=provider,
            )
        _write_disposition_ledgers(
            output, manifest=manifest, checkpoint=checkpoint
        )
        rows = state.get("translations") or []
        primary_apply = _apply_batch(primary_database, rows)
        replay_apply = _apply_batch(replay_database, rows)
        primary_state = sv1b._translation_logical_state(primary_database)
        replay_state = sv1b._translation_logical_state(replay_database)
        if primary_state != replay_state:
            raise LocalizationClosureError(
                "localization_primary_replay_batch_state_mismatch"
            )
        state.update({
            "primary_apply": primary_apply,
            "replay_apply": replay_apply,
            "primary_replay_translation_fingerprint": primary_state["fingerprint"],
            "status": "applied_both",
        })
        checkpoint["batches"][batch_id] = state
        _write_checkpoint(output, checkpoint)
        _write_disposition_ledgers(
            output, manifest=manifest, checkpoint=checkpoint
        )

    total_cost = _checkpoint_cost_upper_bound(checkpoint)
    if total_cost > COST_CAP_USD:
        raise LocalizationClosureError("actual_llm_cost_upper_bound_exceeds_usd10")
    primary_vocabulary, primary_private = sv1b._vocabulary_state(primary_database)
    replay_vocabulary, replay_private = sv1b._vocabulary_state(replay_database)
    if primary_vocabulary != replay_vocabulary or primary_private != replay_private:
        raise LocalizationClosureError("localization_primary_replay_final_state_mismatch")
    excluded = {row["canonical_name"] for row in manifest["explicit_exclusions"]}
    terminal_outcomes = []
    historical_reason_counts: Counter[str] = Counter()
    standard_batch_calls = 0
    item_adjudication_calls = 0
    for batch in manifest["batches"]:
        state = dict(checkpoint["batches"][str(batch["batch_id"])])
        if state.get("status") != "applied_both":
            raise LocalizationClosureError("localization_batch_not_applied_both")
        standard_batch_calls += int(
            state.get("standard_batch_call_count")
            or state.get("attempt_count")
            or 0
        )
        item_adjudication_calls += sum(
            int(value.get("attempt_count") or 0)
            for value in (state.get("item_adjudications") or {}).values()
        )
        validation = state.get("item_validation") or {}
        historical_reason_counts.update(validation.get("per_reason_counts") or {})
        outcomes = state.get("terminal_item_outcomes")
        if outcomes is None:
            outcomes = [
                {
                    "canonical_name": str(row["canonical_name"]),
                    "category": str(row["category"]),
                    "outcome": "accepted_translation",
                    "source": "accepted_prior_batch_checkpoint",
                }
                for row in state.get("translations") or ()
            ]
        terminal_outcomes.extend(outcomes)
    terminal_outcomes = sorted(
        terminal_outcomes, key=lambda row: str(row["canonical_name"])
    )
    eligible_names = {
        str(row["canonical_name"]) for row in manifest["eligible_rows"]
    }
    terminal_names = [str(row["canonical_name"]) for row in terminal_outcomes]
    if (
        len(terminal_names) != len(set(terminal_names))
        or set(terminal_names) != eligible_names
        or any(row.get("outcome") not in TERMINAL_ITEM_OUTCOMES for row in terminal_outcomes)
    ):
        raise LocalizationClosureError("localization_final_item_membership_invalid")
    display_preserved = {
        str(row["canonical_name"])
        for row in terminal_outcomes
        if row.get("outcome") == "explicit_display_preserved_nontranslatable"
    }
    manual_pending = {
        str(row["canonical_name"])
        for row in terminal_outcomes
        if row.get("outcome") == "manual_localization_review_pending"
    }
    manual_overrides = {
        str(row["canonical_name"])
        for row in terminal_outcomes
        if row.get("outcome") == "manual_localization_override"
    }
    accepted_new = {
        str(row["canonical_name"])
        for row in terminal_outcomes
        if row.get("outcome") == "accepted_translation"
    }
    remaining = set(primary_private["blocking_missing_ai_tags"])
    unexplained = sorted(
        remaining
        - excluded
        - display_preserved
        - manual_pending
        - manual_overrides
    )
    if unexplained:
        raise LocalizationClosureError("localization_eligible_missing_after_execution")
    primary_state = sv1b._translation_logical_state(primary_database)
    replay_state = sv1b._translation_logical_state(replay_database)
    if primary_state != replay_state:
        raise LocalizationClosureError("localization_primary_replay_translation_mismatch")
    baseline = sv1b.read_json(output / "localization-baseline-proof.json")
    accepted_prior_count = int(
        (baseline.get("accepted_translation_state") or {}).get("count") or 0
    )
    membership_rows = sorted([
        *terminal_outcomes,
        *[
            {
                "canonical_name": str(row["canonical_name"]),
                "category": str(row["category"]),
                "outcome": "explicit_proper_noun_exclusion",
                "reason_code": str(row["reason_code"]),
            }
            for row in manifest["explicit_exclusions"]
        ],
    ], key=lambda row: (str(row["canonical_name"]), str(row["outcome"])))
    equations = {
        "initial_missing_balanced": (
            manifest["initial_missing_count"]
            == manifest["eligible_translation_count"] + len(excluded)
        ),
        "eligible_outcomes_balanced": (
            manifest["eligible_translation_count"]
            == len(accepted_new)
            + len(display_preserved)
            + len(manual_pending)
            + len(manual_overrides)
        ),
        "translation_count_balanced": (
            int(primary_state["count"]) == accepted_prior_count + len(accepted_new)
        ),
        "terminal_membership_exact": set(terminal_names) == eligible_names,
        "primary_replay_equal": primary_state == replay_state,
        "silently_missing_zero": len(unexplained) == 0,
        "duplicate_disposition_zero": len(terminal_names) == len(set(terminal_names)),
    }
    localization_accounting_closed = all(equations.values())
    if not localization_accounting_closed:
        raise LocalizationClosureError("localization_closure_equation_failed")
    localization_translation_complete = not manual_pending
    downstream_progression_allowed = bool(
        localization_accounting_closed
        and len(manual_pending) <= MAX_MANUAL_REVIEW_PENDING_FOR_DOWNSTREAM
    )
    projected_item_cost = _cost_upper_bound(
        manifest["eligible_translation_count"]
        * TOKENS_PER_ITEM_ADJUDICATION_UPPER_BOUND
    )
    projected_total_cost = round(
        float(manifest["projected_cost_upper_bound_usd"])
        + projected_item_cost,
        6,
    )
    if projected_total_cost > COST_CAP_USD:
        raise LocalizationClosureError("projected_item_level_cost_exceeds_usd10")
    governed_vocabulary = dict(primary_vocabulary)
    governed_vocabulary.update({
        "raw_blocking_missing_ai_translation_count": int(
            primary_vocabulary.get("blocking_missing_ai_translation_count") or 0
        ),
        "explicit_display_preserved_count": len(display_preserved),
        "manual_localization_review_pending_count": len(manual_pending),
        "blocking_missing_ai_translation_count": len(manual_pending),
    })
    final_pending_rows = [
        row for row in terminal_outcomes
        if row.get("outcome") == "manual_localization_review_pending"
    ]
    final_pending_reasons = Counter(
        str(row.get("validator_verdict") or "unexplained")
        for row in final_pending_rows
    )
    disposition_ledger = _write_disposition_ledgers(
        output, manifest=manifest, checkpoint=checkpoint
    )
    salvaged_valid_count = 0
    for state in checkpoint["batches"].values():
        reasons = dict(
            (state.get("item_validation") or {}).get("per_reason_counts") or {}
        )
        if any(
            int(count or 0) > 0
            for reason, count in reasons.items()
            if reason != "accepted_translation"
        ):
            salvaged_valid_count += int(
                reasons.get("accepted_translation") or 0
            )
    new_standard_calls = standard_batch_calls - resume_metrics["standard_call_count"]
    new_item_calls = (
        item_adjudication_calls - resume_metrics["targeted_call_count"]
    )

    result = {
        "passed": True,
        "status": (
            "localization_accounting_closed"
            if downstream_progression_allowed
            else "blocked_sv1b_systemic_localization_quality"
        ),
        "policy_version": POLICY_VERSION,
        "item_validation_policy_version": ITEM_VALIDATION_POLICY_VERSION,
        "display_preserve_policy_version": DISPLAY_PRESERVE_POLICY_VERSION,
        "targeted_adjudication_prompt_version": TARGETED_ADJUDICATION_PROMPT_VERSION,
        "manual_review_policy_version": MANUAL_REVIEW_POLICY_VERSION,
        "manual_review_pending_threshold": (
            MAX_MANUAL_REVIEW_PENDING_FOR_DOWNSTREAM
        ),
        "approved_model": APPROVED_MODEL,
        "pricing_policy_version": PRICING_POLICY_VERSION,
        "manifest_fingerprint": manifest["manifest_fingerprint"],
        "vocabulary": governed_vocabulary,
        "accepted_translation_state": primary_state,
        "initial_missing_ai_tag_count": manifest["initial_missing_count"],
        "initial_eligible_count": manifest["eligible_translation_count"],
        "accepted_prior_translation_count": accepted_prior_count,
        "accepted_new_translation_count": len(accepted_new),
        "explicit_proper_noun_exclusion_count": len(excluded),
        "explicit_display_preserved_count": len(display_preserved),
        "manual_localization_review_pending_count": len(manual_pending),
        "manual_localization_override_count": len(manual_overrides),
        "explicit_nontranslatable_exclusion_count": (
            len(excluded) + len(display_preserved)
        ),
        "eligible_ai_tag_missing_count": 0,
        "silently_missing_eligible_count": 0,
        "missing_disposition_count": 0,
        "duplicate_disposition_count": 0,
        "localization_ambiguity_count": int(
            final_pending_reasons.get("ambiguous_needs_review") or 0
        ),
        "final_untranslated_echo_count": int(
            final_pending_reasons.get("untranslated_echo") or 0
        ),
        "final_missing_result_count": int(
            final_pending_reasons.get("missing_result") or 0
        ),
        "final_invalid_display_count": int(
            final_pending_reasons.get("invalid_display") or 0
        ),
        "final_invalid_aliases_count": int(
            final_pending_reasons.get("invalid_aliases") or 0
        ),
        "final_unexpected_result_count": int(
            final_pending_reasons.get("unexpected_result") or 0
        ),
        "final_duplicate_result_count": int(
            final_pending_reasons.get("duplicate_result") or 0
        ),
        "manual_review_pending_reason_counts": dict(
            sorted(final_pending_reasons.items())
        ),
        "historical_item_validation_reason_counts": dict(
            sorted(historical_reason_counts.items())
        ),
        "standard_batch_call_count": standard_batch_calls,
        "item_adjudication_call_count": item_adjudication_calls,
        "new_standard_batch_call_count": new_standard_calls,
        "new_item_adjudication_call_count": new_item_calls,
        "reused_applied_translation_count": resume_metrics[
            "applied_translation_count"
        ],
        "reused_targeted_result_count": resume_metrics[
            "targeted_accepted_result_count"
        ],
        "salvaged_valid_result_count": salvaged_valid_count,
        "avoided_duplicate_call_count": resume_metrics[
            "avoided_duplicate_call_count"
        ],
        "provider_source_localized_count": 0,
        "creator_identity_translated_count": 0,
        "provider_tags_written_to_media_tags_count": 0,
        "original_provider_text_preserved": True,
        "external_llm_call_count": standard_batch_calls + item_adjudication_calls,
        "projected_llm_cost_upper_bound_usd": manifest["projected_cost_upper_bound_usd"],
        "projected_item_adjudication_cost_upper_bound_usd": projected_item_cost,
        "projected_total_item_level_cost_upper_bound_usd": projected_total_cost,
        "actual_llm_cost_upper_bound_usd": total_cost,
        "projected_and_actual_llm_cost_usd": max(
            projected_total_cost, total_cost
        ),
        "fallback_provider_used": False,
        "image_upload_count": 0,
        "atomic_checkpoint_resume_used": True,
        "restart_safe_dual_database_reconciliation_used": True,
        "primary_replay_translation_fingerprint_equal": True,
        "localization_membership_fingerprint": sv1b.sha256_payload(
            membership_rows
        ),
        "localization_equations": equations,
        "disposition_ledger": disposition_ledger,
        "transport_logging": transport_logging,
        "localization_accounting_closed": localization_accounting_closed,
        "localization_translation_complete": localization_translation_complete,
        "downstream_progression_allowed": downstream_progression_allowed,
        "localization_complete": localization_translation_complete,
    }
    sv1b.write_json(output / "localization-closure-proof.json", result)
    return result
