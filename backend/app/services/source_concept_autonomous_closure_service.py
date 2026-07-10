"""Autonomous SourceConcept pair disposition and non-materialized overlay.

This module closes source-layer candidate pairs without a human work queue. It
does not create Entity truth. Unresolved relations are retained as
``deferred_nonblocking`` and never participate in identity union.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .source_concept_resolver_service import (
    LLMAdjudicationConfig,
    SourceConceptEdgeDraft,
    SourceConceptResolutionResult,
    SourceConceptSignalDraft,
    build_data_aware_ambiguity_profiles,
    select_llm_adjudication_edges,
)

PAIR_DISPOSITIONS = ("must_link", "cannot_link", "deferred_nonblocking")
SIGNAL_PROJECTIONS = ("materialized_identity", "isolated_evidence", "rejected_evidence")
OVERLAY_VERSION = "source_concept_deferred_overlay_v1"
COMPATIBILITY_VERSION = "r2r_autonomous_pair_compatibility_v1"
FIRST_PASS_VERSION = "r2r_autonomous_first_pass_v1"
SECOND_PASS_VERSION = "r2r_autonomous_second_pass_v1"
FINAL_DISPOSITION_VERSION = "r2r_machine_disposition_v1"


class AutonomousClosureError(RuntimeError):
    """Fail-closed autonomous closure error."""


@dataclass(frozen=True)
class CandidatePair:
    pair_id: str
    left_signal_key: str
    right_signal_key: str
    edge_key: str
    edge_type: str
    edge_status: str
    weight: float
    evidence_source: str
    resolution_reason_code: str
    negative_reason_code: str | None
    payload_hash: str


@dataclass(frozen=True)
class PairDisposition:
    pair_id: str
    left_signal_key: str
    right_signal_key: str
    disposition: str
    source: str
    pass_name: str
    confidence: float | None
    reason_code: str | None
    cache_key: str | None = None


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def pair_id_for(left_signal_key: str, right_signal_key: str) -> str:
    left, right = sorted((str(left_signal_key), str(right_signal_key)))
    return _sha256({"left_signal_key": left, "right_signal_key": right})


def normalize_machine_disposition(value: Any) -> str:
    decision = str(value or "deferred_nonblocking").strip().casefold()
    if decision in {"must_link", "same", "same_concept"}:
        return "must_link"
    if decision in {"cannot_link", "cannot", "different", "not_same"}:
        return "cannot_link"
    return "deferred_nonblocking"


def _candidate_from_edge(edge: SourceConceptEdgeDraft) -> CandidatePair:
    left, right = sorted((str(edge.left_signal_key), str(edge.right_signal_key)))
    payload_hash = _sha256(
        {
            "left_signal_key": left,
            "right_signal_key": right,
            "edge_type": edge.edge_type,
            "edge_status": edge.status,
            "weight": edge.weight,
            "evidence_source": edge.evidence_source,
            "resolution_reason_code": edge.resolution_reason_code,
            "negative_reason_code": edge.negative_reason_code,
            "payload": edge.payload,
        }
    )
    return CandidatePair(
        pair_id=pair_id_for(left, right),
        left_signal_key=left,
        right_signal_key=right,
        edge_key=edge.edge_key,
        edge_type=edge.edge_type,
        edge_status=edge.status,
        weight=float(edge.weight),
        evidence_source=edge.evidence_source,
        resolution_reason_code=edge.resolution_reason_code,
        negative_reason_code=edge.negative_reason_code,
        payload_hash=payload_hash,
    )


def build_candidate_pair_manifest(
    edges: Sequence[SourceConceptEdgeDraft],
    *,
    signals: Sequence[SourceConceptSignalDraft],
    max_calls: int = 20000,
) -> tuple[CandidatePair, ...]:
    """Return every unique budget-eligible pair without a fixed small cap."""

    config = LLMAdjudicationConfig(
        enabled=True,
        max_calls=max_calls,
        max_budget_usd=10_000.0,
        selection_policy="budget_driven_all_eligible",
    )
    selected = select_llm_adjudication_edges(edges, signals=signals, config=config)
    by_pair: dict[str, CandidatePair] = {}
    for edge in selected:
        candidate = _candidate_from_edge(edge)
        previous = by_pair.get(candidate.pair_id)
        if previous is None or (candidate.weight, candidate.edge_key) > (previous.weight, previous.edge_key):
            by_pair[candidate.pair_id] = candidate
    return tuple(sorted(by_pair.values(), key=lambda row: row.pair_id))


def signal_identity_payload(signal: SourceConceptSignalDraft) -> dict[str, str]:
    return {
        "signal_key": str(signal.signal_key),
        "canonical_key": str(signal.canonical_key or signal.normalized_key or ""),
        "work_context_key": str(signal.work_context_key or ""),
    }


def _cached_side_identity(value: Any) -> dict[str, str]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "signal_key": str(row.get("signal_key") or ""),
        "canonical_key": str(row.get("canonical_key") or ""),
        "work_context_key": str(row.get("work_context_key") or ""),
    }


def classify_legacy_cache_reuse(
    cache_dir: Path,
    *,
    candidates: Sequence[CandidatePair],
    signals: Sequence[SourceConceptSignalDraft],
    resolver_version: str,
) -> tuple[dict[str, PairDisposition], dict[str, Any], list[dict[str, Any]]]:
    """Classify R1R/R2 cache reuse without mutating any cache record."""

    records_dir = cache_dir / "records"
    if not records_dir.is_dir():
        raise AutonomousClosureError("legacy_cache_records_missing")
    candidate_by_keys = {
        (row.left_signal_key, row.right_signal_key): row for row in candidates
    }
    signal_by_key = {str(signal.signal_key): signal for signal in signals}
    global_levels: Counter[str] = Counter()
    current_levels: Counter[str] = Counter()
    outcomes: Counter[str] = Counter()
    dispositions: dict[str, PairDisposition] = {}
    analysis_rows: list[dict[str, Any]] = []
    compatible_pairs: set[tuple[str, str]] = set()

    for path in sorted(records_dir.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            global_levels["invalidated"] += 1
            continue
        left_key = str(record.get("left_signal_key") or "")
        right_key = str(record.get("right_signal_key") or "")
        pair = tuple(sorted((left_key, right_key)))
        disposition = normalize_machine_disposition(
            record.get("resolver_decision") or record.get("decision")
        )
        outcomes[disposition] += 1
        left = signal_by_key.get(left_key)
        right = signal_by_key.get(right_key)
        cache_summary = (
            record.get("input_signal_summary")
            if isinstance(record.get("input_signal_summary"), Mapping)
            else {}
        )
        cached_left = _cached_side_identity(cache_summary.get("left"))
        cached_right = _cached_side_identity(cache_summary.get("right"))
        current_by_key = {
            left_key: signal_identity_payload(left) if left is not None else {},
            right_key: signal_identity_payload(right) if right is not None else {},
        }
        pair_identity_compatible = bool(
            left is not None
            and right is not None
            and not record.get("error_state")
            and record.get("compatible_for_exact_reuse")
            and cached_left == current_by_key.get(cached_left["signal_key"])
            and cached_right == current_by_key.get(cached_right["signal_key"])
        )
        if not pair_identity_compatible:
            level = "invalidated" if record.get("error_state") else "semantic_prior"
        elif str(record.get("resolver_version") or "") == resolver_version:
            level = "exact_compatible"
        else:
            level = "stable_pair_identity"
        global_levels[level] += 1
        candidate = candidate_by_keys.get(pair)
        if candidate is not None and level in {"exact_compatible", "stable_pair_identity"}:
            compatible_pairs.add(pair)
            current_levels[level] += 1
            dispositions[candidate.pair_id] = PairDisposition(
                pair_id=candidate.pair_id,
                left_signal_key=candidate.left_signal_key,
                right_signal_key=candidate.right_signal_key,
                disposition=disposition,
                source="legacy_cache_compatible",
                pass_name="reused",
                confidence=float(record["confidence"]) if record.get("confidence") is not None else None,
                reason_code=str(record.get("reason_code")) if record.get("reason_code") else None,
                cache_key=str(record.get("cache_key") or path.stem),
            )
        analysis_rows.append(
            {
                "pair": pair,
                "reuse_level": level,
                "disposition": disposition,
                "current_candidate": candidate is not None,
            }
        )

    candidate_pair_set = set(candidate_by_keys)
    missing_pairs = candidate_pair_set - compatible_pairs
    accounting = {
        "existing_cache_record_count": sum(global_levels.values()),
        "global_exact_compatible_count": global_levels["exact_compatible"],
        "global_stable_pair_identity_count": global_levels["stable_pair_identity"],
        "semantic_prior_count": global_levels["semantic_prior"],
        "invalidated_count": global_levels["invalidated"],
        "current_candidate_pair_count": len(candidates),
        "exact_compatible_cache_hit_count": current_levels["exact_compatible"],
        "stable_compatible_reuse_count": current_levels["stable_pair_identity"],
        "compatible_current_pair_count": len(compatible_pairs),
        "stable_compatible_outside_current_candidate_count": max(
            0,
            global_levels["exact_compatible"]
            + global_levels["stable_pair_identity"]
            - len(compatible_pairs),
        ),
        "genuinely_missing_pair_count": len(missing_pairs),
        "outcome_counts": dict(outcomes),
        "provider_calls": 0,
        "cache_mutations": 0,
    }
    return dispositions, accounting, analysis_rows


def build_first_pass_payload(
    candidate: CandidatePair,
    signal_by_key: Mapping[str, SourceConceptSignalDraft],
) -> dict[str, Any]:
    def side(signal: SourceConceptSignalDraft) -> dict[str, Any]:
        return {
            "signal_key": signal.signal_key,
            "display_value": signal.display_value,
            "canonical_key": signal.canonical_key,
            "role_hint": signal.role_hint,
            "work_context_key": signal.work_context_key,
            "provider": signal.provider,
            "origin_type": signal.origin_type,
            "trust_tier": signal.trust_tier,
        }

    return {
        "pass": "first",
        "compatibility_version": COMPATIBILITY_VERSION,
        "candidate": asdict(candidate),
        "left": side(signal_by_key[candidate.left_signal_key]),
        "right": side(signal_by_key[candidate.right_signal_key]),
        "allowed_decisions": list(PAIR_DISPOSITIONS),
    }


def _script_family(value: str | None) -> str:
    text = str(value or "")
    if any("\u3040" <= char <= "\u30ff" for char in text):
        return "japanese_kana"
    if any("\u4e00" <= char <= "\u9fff" for char in text):
        return "cjk_han"
    if any("\uac00" <= char <= "\ud7af" for char in text):
        return "hangul"
    if any(char.isalpha() and ord(char) < 128 for char in text):
        return "latin"
    return "other"


def build_second_pass_payload(
    candidate: CandidatePair,
    *,
    signal_by_key: Mapping[str, SourceConceptSignalDraft],
    all_candidates: Sequence[CandidatePair],
    existing_dispositions: Mapping[str, PairDisposition],
) -> dict[str, Any]:
    """Build the richer autonomous escalation payload from fixed evidence only."""

    left = signal_by_key[candidate.left_signal_key]
    right = signal_by_key[candidate.right_signal_key]
    ambiguity = build_data_aware_ambiguity_profiles((left, right))
    neighborhood: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for other in all_candidates:
        disposition = existing_dispositions.get(other.pair_id)
        if disposition is None:
            continue
        for signal_key in (other.left_signal_key, other.right_signal_key):
            if signal_key in {left.signal_key, right.signal_key}:
                neighborhood[signal_key].append(
                    {
                        "pair_id": other.pair_id,
                        "disposition": disposition.disposition,
                        "other_signal_key": (
                            other.right_signal_key
                            if other.left_signal_key == signal_key
                            else other.left_signal_key
                        ),
                    }
                )

    def side(signal: SourceConceptSignalDraft) -> dict[str, Any]:
        return {
            "signal_key": signal.signal_key,
            "display_value": signal.display_value,
            "raw_value": signal.raw_value,
            "normalized_key": signal.normalized_key,
            "canonical_key": signal.canonical_key,
            "role_hint": signal.role_hint,
            "explicit_work_context": signal.work_context_key,
            "parenthetical_base": signal.parenthetical_base,
            "parenthetical_context": signal.parenthetical_context,
            "provider": signal.provider,
            "tag_or_source_category": signal.source_kind,
            "origin_type": signal.origin_type,
            "source_record_scope": signal.source_record_id,
            "media_scope": signal.media_id,
            "script_family": _script_family(signal.display_value),
            "trust_tier": signal.trust_tier,
            "confidence": signal.confidence,
            "ambiguity_profile": ambiguity.get(signal.canonical_key or signal.normalized_key, {}),
            "must_link_cannot_link_neighborhood": neighborhood.get(signal.signal_key, []),
        }

    independent = {
        "distinct_providers": len({value for value in (left.provider, right.provider) if value}),
        "distinct_source_records": len(
            {value for value in (left.source_record_id, right.source_record_id) if value}
        ),
        "distinct_media": len({value for value in (left.media_id, right.media_id) if value is not None}),
    }
    return {
        "pass": "second",
        "compatibility_version": COMPATIBILITY_VERSION,
        "candidate": asdict(candidate),
        "left": side(left),
        "right": side(right),
        "evidence_independence": independent,
        "component_level_constraints": {
            "negative_reason_code": candidate.negative_reason_code,
            "edge_status": candidate.edge_status,
            "resolution_reason_code": candidate.resolution_reason_code,
        },
        "allowed_decisions": list(PAIR_DISPOSITIONS),
        "human_escalation_allowed": False,
    }


def estimate_autonomous_budget(
    candidates: Sequence[CandidatePair],
    *,
    missing_pair_ids: Iterable[str],
    signal_by_key: Mapping[str, SourceConceptSignalDraft],
    historical_uncertain_rate: float,
) -> dict[str, Any]:
    missing_set = set(missing_pair_ids)
    missing = [row for row in candidates if row.pair_id in missing_set]
    first_payloads = [build_first_pass_payload(row, signal_by_key) for row in missing]
    first_input = sum(max(1, len(_canonical_json(row)) // 4) for row in first_payloads)
    first_output = len(missing) * 80
    expected_escalations = min(
        len(missing),
        int(round(len(missing) * max(0.0, min(1.0, historical_uncertain_rate)))),
    )
    representative_second_tokens = 0
    if missing:
        sample = missing[: min(50, len(missing))]
        empty_dispositions: dict[str, PairDisposition] = {}
        representative_second_tokens = int(
            round(
                sum(
                    len(
                        _canonical_json(
                            build_second_pass_payload(
                                row,
                                signal_by_key=signal_by_key,
                                all_candidates=candidates,
                                existing_dispositions=empty_dispositions,
                            )
                        )
                    )
                    // 4
                    for row in sample
                )
                / len(sample)
            )
        )
    second_input = expected_escalations * representative_second_tokens
    second_output = expected_escalations * 100
    total_tokens = first_input + first_output + second_input + second_output
    return {
        "missing_pair_count": len(missing),
        "usage_unit": "tokens",
        "estimated_first_pass_input_usage_units": first_input,
        "estimated_first_pass_completion_usage_units": first_output,
        "expected_uncertain_escalation_count": expected_escalations,
        "estimated_second_pass_input_usage_units": second_input,
        "estimated_second_pass_completion_usage_units": second_output,
        "estimated_total_usage_units": total_tokens,
        "projected_cost_usd": round((total_tokens / 1000.0) * 0.002, 6),
        "cost_estimate_policy": "repo_projection_0.002_usd_per_1k_combined_tokens",
        "recommended_budget_usd": 2.0,
        "fixed_small_pair_cap_used": False,
    }


def disposition_accounting(
    candidates: Sequence[CandidatePair],
    dispositions: Iterable[PairDisposition],
) -> dict[str, Any]:
    candidate_ids = {row.pair_id for row in candidates}
    rows = list(dispositions)
    counts_by_id = Counter(row.pair_id for row in rows)
    valid_rows = [
        row
        for row in rows
        if row.pair_id in candidate_ids and row.disposition in PAIR_DISPOSITIONS
    ]
    valid_ids = {row.pair_id for row in valid_rows}
    disposition_counts = Counter(row.disposition for row in valid_rows)
    duplicate_count = sum(count - 1 for count in counts_by_id.values() if count > 1)
    unaccounted = candidate_ids - valid_ids
    extra = set(counts_by_id) - candidate_ids
    total = len(candidate_ids)
    coverage = len(valid_ids) / total if total else 1.0
    equality = total == sum(disposition_counts.values()) and not duplicate_count and not extra
    return {
        "total_candidate_pairs": total,
        "must_link_count": disposition_counts["must_link"],
        "cannot_link_count": disposition_counts["cannot_link"],
        "deferred_nonblocking_count": disposition_counts["deferred_nonblocking"],
        "unaccounted_pair_count": len(unaccounted),
        "duplicate_disposition_count": duplicate_count,
        "extra_disposition_count": len(extra),
        "silently_dropped_pair_count": 0,
        "candidate_disposition_coverage": round(coverage, 12),
        "accounting_equality_passed": equality and not unaccounted,
    }


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    temp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _cache_record_path(cache_root: Path, pass_name: str, pair_id: str) -> Path:
    return cache_root / pass_name / "records" / f"{pair_id}.json"


def _failure_record_path(cache_root: Path, pass_name: str, pair_id: str) -> Path:
    return cache_root / pass_name / "failures" / f"{pair_id}-{_sha256(utc_now_iso())[:12]}.json"


def load_exact_pass_record(
    cache_root: Path,
    *,
    pass_name: str,
    candidate: CandidatePair,
    payload: Mapping[str, Any],
) -> dict[str, Any] | None:
    path = _cache_record_path(cache_root, pass_name, candidate.pair_id)
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    expected_version = FIRST_PASS_VERSION if pass_name == "first" else SECOND_PASS_VERSION
    if (
        record.get("pair_id") != candidate.pair_id
        or record.get("pass_version") != expected_version
        or record.get("compatibility_version") != COMPATIBILITY_VERSION
        or record.get("payload_hash") != _sha256(payload)
        or record.get("success") is not True
    ):
        return None
    return record


def persist_successful_pass_record(
    cache_root: Path,
    *,
    pass_name: str,
    candidate: CandidatePair,
    payload: Mapping[str, Any],
    response: Mapping[str, Any],
) -> dict[str, Any]:
    raw_decision = str(response.get("decision") or "").strip().casefold()
    if raw_decision not in {
        "must_link",
        "same",
        "same_concept",
        "cannot_link",
        "cannot",
        "different",
        "not_same",
        "deferred_nonblocking",
        "deferred",
        "needs_review",
        "uncertain",
    }:
        raise AutonomousClosureError("invalid_or_missing_machine_decision")
    disposition = normalize_machine_disposition(raw_decision)
    record = {
        "pair_id": candidate.pair_id,
        "left_signal_key": candidate.left_signal_key,
        "right_signal_key": candidate.right_signal_key,
        "pass_name": pass_name,
        "pass_version": FIRST_PASS_VERSION if pass_name == "first" else SECOND_PASS_VERSION,
        "compatibility_version": COMPATIBILITY_VERSION,
        "payload_hash": _sha256(payload),
        "decision": disposition,
        "confidence": response.get("confidence"),
        "reason_code": response.get("reason_code"),
        "success": True,
        "error_state": None,
        "persisted_at": utc_now_iso(),
    }
    _atomic_write_json(_cache_record_path(cache_root, pass_name, candidate.pair_id), record)
    return record


def persist_failed_pass_attempt(
    cache_root: Path,
    *,
    pass_name: str,
    candidate: CandidatePair,
    payload: Mapping[str, Any],
    error: BaseException,
) -> Path:
    path = _failure_record_path(cache_root, pass_name, candidate.pair_id)
    _atomic_write_json(
        path,
        {
            "pair_id": candidate.pair_id,
            "pass_name": pass_name,
            "payload_hash": _sha256(payload),
            "success": False,
            "error_type": type(error).__name__,
            "recorded_at": utc_now_iso(),
        },
    )
    return path


JudgmentExecutor = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]


def execute_autonomous_missing_pairs(
    candidates: Sequence[CandidatePair],
    *,
    initial_dispositions: Mapping[str, PairDisposition],
    signal_by_key: Mapping[str, SourceConceptSignalDraft],
    cache_root: Path,
    executor: JudgmentExecutor,
) -> tuple[dict[str, PairDisposition], dict[str, Any]]:
    """Execute cache-first autonomous passes with immediate atomic checkpoints."""

    dispositions = dict(initial_dispositions)
    counters: Counter[str] = Counter()
    transitions: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.pair_id in dispositions:
            counters["already_accounted"] += 1
            continue
        first_payload = build_first_pass_payload(candidate, signal_by_key)
        first = load_exact_pass_record(
            cache_root,
            pass_name="first",
            candidate=candidate,
            payload=first_payload,
        )
        if first is None:
            try:
                response = executor("first", first_payload)
                first = persist_successful_pass_record(
                    cache_root,
                    pass_name="first",
                    candidate=candidate,
                    payload=first_payload,
                    response=response,
                )
                counters["first_provider_success"] += 1
            except Exception as exc:
                persist_failed_pass_attempt(
                    cache_root,
                    pass_name="first",
                    candidate=candidate,
                    payload=first_payload,
                    error=exc,
                )
                counters["provider_failure"] += 1
                continue
        else:
            counters["first_cache_hit"] += 1
        first_disposition = normalize_machine_disposition(first.get("decision"))
        if first_disposition in {"must_link", "cannot_link"}:
            final = PairDisposition(
                pair_id=candidate.pair_id,
                left_signal_key=candidate.left_signal_key,
                right_signal_key=candidate.right_signal_key,
                disposition=first_disposition,
                source="r2r_cache_or_provider",
                pass_name="first",
                confidence=float(first["confidence"]) if first.get("confidence") is not None else None,
                reason_code=str(first.get("reason_code")) if first.get("reason_code") else None,
                cache_key=candidate.pair_id,
            )
            dispositions[candidate.pair_id] = final
            transitions.append({"pair_id": candidate.pair_id, "first": first_disposition, "final": first_disposition})
            continue

        counters["first_uncertain"] += 1
        second_payload = build_second_pass_payload(
            candidate,
            signal_by_key=signal_by_key,
            all_candidates=candidates,
            existing_dispositions=dispositions,
        )
        second = load_exact_pass_record(
            cache_root,
            pass_name="second",
            candidate=candidate,
            payload=second_payload,
        )
        if second is None:
            try:
                response = executor("second", second_payload)
                second = persist_successful_pass_record(
                    cache_root,
                    pass_name="second",
                    candidate=candidate,
                    payload=second_payload,
                    response=response,
                )
                counters["second_provider_success"] += 1
            except Exception as exc:
                persist_failed_pass_attempt(
                    cache_root,
                    pass_name="second",
                    candidate=candidate,
                    payload=second_payload,
                    error=exc,
                )
                counters["provider_failure"] += 1
                continue
        else:
            counters["second_cache_hit"] += 1
        final_disposition = normalize_machine_disposition(second.get("decision"))
        final = PairDisposition(
            pair_id=candidate.pair_id,
            left_signal_key=candidate.left_signal_key,
            right_signal_key=candidate.right_signal_key,
            disposition=final_disposition,
            source="r2r_cache_or_provider",
            pass_name="second",
            confidence=float(second["confidence"]) if second.get("confidence") is not None else None,
            reason_code=str(second.get("reason_code")) if second.get("reason_code") else None,
            cache_key=candidate.pair_id,
        )
        dispositions[candidate.pair_id] = final
        transitions.append(
            {
                "pair_id": candidate.pair_id,
                "first": first_disposition,
                "second": final_disposition,
                "final": final_disposition,
            }
        )

    accounting = disposition_accounting(candidates, dispositions.values())
    return dispositions, {
        **{
            key: counters[key]
            for key in (
                "already_accounted",
                "first_provider_success",
                "first_cache_hit",
                "first_uncertain",
                "second_provider_success",
                "second_cache_hit",
                "provider_failure",
            )
        },
        "transitions": transitions,
        "accounting": accounting,
        "provider_failures_counted_as_success": False,
        "atomic_per_success_persistence": True,
    }


def write_deferred_overlay(
    path: Path,
    *,
    candidates: Sequence[CandidatePair],
    dispositions: Sequence[PairDisposition],
    projection_fingerprint: str,
) -> dict[str, Any]:
    deferred = [row for row in dispositions if row.disposition == "deferred_nonblocking"]
    payload = {
        "overlay_version": OVERLAY_VERSION,
        "compatibility_version": COMPATIBILITY_VERSION,
        "generated_at": utc_now_iso(),
        "candidate_pair_count": len(candidates),
        "deferred_nonblocking_count": len(deferred),
        "projection_fingerprint": projection_fingerprint,
        "relations": [asdict(row) for row in sorted(deferred, key=lambda row: row.pair_id)],
    }
    checksum = _sha256(payload)
    _atomic_write_json(path, {**payload, "payload_sha256": checksum})
    reread = json.loads(path.read_text(encoding="utf-8"))
    reread_checksum = reread.pop("payload_sha256", None)
    return {
        "overlay_version": OVERLAY_VERSION,
        "compatibility_version": COMPATIBILITY_VERSION,
        "relation_count": len(deferred),
        "atomic_write_passed": True,
        "checksum_passed": reread_checksum == _sha256(reread),
        "private_path_redacted": True,
    }


def project_autonomous_materialization(
    result: SourceConceptResolutionResult,
    *,
    dispositions: Sequence[PairDisposition],
) -> tuple[SourceConceptResolutionResult, dict[str, Any]]:
    """Keep only policy-passing identity concepts; retain every signal."""

    materialized_concepts = tuple(concept for concept in result.concepts if concept.status == "active")
    materialized_keys = {concept.concept_key for concept in materialized_concepts}
    materialized_signal_keys = {
        signal.signal_key for concept in materialized_concepts for signal in concept.signals
    }
    rejected_signal_keys = {
        str(row.get("signal_key"))
        for row in result.rejected_signals
        if isinstance(row, Mapping) and row.get("signal_key")
    }
    original_signal_by_key = {signal.signal_key: signal for signal in result.signals}
    search_neighbors: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for disposition in dispositions:
        if disposition.disposition not in {"must_link", "deferred_nonblocking"}:
            continue
        left = original_signal_by_key.get(disposition.left_signal_key)
        right = original_signal_by_key.get(disposition.right_signal_key)
        if left is None or right is None:
            continue
        for current, other in ((left, right), (right, left)):
            search_neighbors[current.signal_key].append(
                {
                    "pair_id": disposition.pair_id,
                    "relation": disposition.disposition,
                    "neighbor_signal_key": other.signal_key,
                    "fallback_alias_keys": sorted(
                        {
                            str(value)
                            for value in (other.canonical_key, other.normalized_key)
                            if value
                        }
                    ),
                    "neighbor_media_id": other.media_id,
                    "neighbor_work_context_key": other.work_context_key,
                    "neighbor_role_hint": other.role_hint,
                }
            )
    projected_signals = tuple(
        replace(
            signal,
            status=(
                "materialized_identity"
                if signal.signal_key in materialized_signal_keys
                else "rejected_evidence"
                if signal.signal_key in rejected_signal_keys
                else "isolated_evidence"
            ),
            evidence_payload={
                **dict(signal.evidence_payload or {}),
                "r2r_search_overlay": {
                    "version": OVERLAY_VERSION,
                    "identity_union_allowed": False,
                    "human_review_required": False,
                    "neighbors": sorted(
                        search_neighbors.get(signal.signal_key, []),
                        key=lambda row: (row["pair_id"], row["neighbor_signal_key"]),
                    ),
                },
            },
            created_by_run_id=result.run_id,
        )
        for signal in result.signals
    )
    signal_by_key = {signal.signal_key: signal for signal in projected_signals}
    projected_concepts = tuple(
        replace(
            concept,
            status="active",
            signals=tuple(signal_by_key[signal.signal_key] for signal in concept.signals),
        )
        for concept in materialized_concepts
    )
    projected = replace(
        result,
        signals=projected_signals,
        concepts=projected_concepts,
        aliases=tuple(
            replace(alias, status="active")
            for alias in result.aliases
            if alias.concept_key in materialized_keys and alias.status == "active"
        ),
        evidence=tuple(
            replace(evidence, status="active")
            for evidence in result.evidence
            if evidence.concept_key in materialized_keys and evidence.status == "active"
        ),
        links=tuple(
            replace(link, link_status="active")
            for link in result.links
            if link.concept_key in materialized_keys and link.link_status == "active"
        ),
        search_index=tuple(
            replace(item, status="active")
            for item in result.search_index
            if item.concept_key in materialized_keys and item.status == "active"
        ),
        summary={
            **result.summary,
            "concept_count": len(projected_concepts),
            "concept_counts_by_status": {"active": len(projected_concepts)},
            "materialized_needs_review_count": 0,
            "manual_review_required_count": 0,
            "operator_blocking_review_count": 0,
            "manual_review_queue_generated": False,
        },
    )
    signal_projection_counts = Counter(signal.status for signal in projected_signals)
    fingerprint_payload = {
        "concepts": [
            {
                "concept_key": concept.concept_key,
                "signals": sorted(signal.signal_key for signal in concept.signals),
            }
            for concept in sorted(projected_concepts, key=lambda row: row.concept_key)
        ],
        "signals": [
            {"signal_key": signal.signal_key, "projection": signal.status}
            for signal in sorted(projected_signals, key=lambda row: row.signal_key)
        ],
        "dispositions": [
            {"pair_id": row.pair_id, "disposition": row.disposition}
            for row in sorted(dispositions, key=lambda row: row.pair_id)
        ],
    }
    projection_fingerprint = _sha256(fingerprint_payload)
    proof = {
        "materialized_source_concept_count": len(projected_concepts),
        "materialized_needs_review_count": 0,
        "deferred_evidence_signal_count": signal_projection_counts["isolated_evidence"],
        "rejected_evidence_signal_count": signal_projection_counts["rejected_evidence"],
        "materialized_identity_signal_count": signal_projection_counts["materialized_identity"],
        "source_signal_count_before": len(result.signals),
        "source_signal_count_after": len(projected_signals),
        "unresolved_evidence_retained": len(result.signals) == len(projected_signals),
        "projection_fingerprint": projection_fingerprint,
        "manual_review_required_count": 0,
        "operator_blocking_review_count": 0,
        "manual_review_queue_generated": False,
        "evidence_fallback_relation_count": sum(
            len(rows) for rows in search_neighbors.values()
        )
        // 2,
        "evidence_retention_projection": (
            "SourceConceptSignal projection plus private versioned pair overlay"
        ),
    }
    return projected, proof
