"""Deterministic multilingual creator identity closure for SourceConcept.

The service is deliberately source-layer only.  It turns trusted provider
creator identities into a star graph whose centre is a stable provider ID and
whose leaves are trusted creator names/accounts.  Search aliases remain
separate from identity evidence and no Entity or media-tag truth is written.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

from .source_metadata_registry_service import canonical_source_key, normalize_source_text


CREATOR_COMPATIBLE_ROLES = frozenset({"artist", "creator"})
PAIR_DISPOSITIONS = frozenset({"must_link", "cannot_link", "deferred_nonblocking"})
RESOLVER_VERSION = "ml2_stable_creator_identity_closure_v1"
POLICY_VERSION = "ml2_multilingual_identity_candidate_policy_v1"
LLM_POLICY_VERSION = "bounded_phase_primary_llm_usd10_v1"
GAP_REASON_CODES = frozenset(
    {
        "trusted_creator_id_signal_missing",
        "trusted_creator_name_signal_missing",
        "trusted_creator_account_signal_missing",
        "identity_anchor_not_generated",
        "role_classification_loss",
        "candidate_blocking_miss",
        "existing_sourceconcept_consumption_gap",
        "accepted_cannot_link_exclusion",
        "stale_schema_or_migration",
        "benchmark_family_not_identity_eligible",
        "insufficient_trusted_evidence",
        "other_explained_nonblocking",
    }
)


class CreatorIdentityClosureError(RuntimeError):
    """Raised when deterministic identity closure cannot proceed safely."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def stable_identity_fingerprint(provider: str, stable_creator_id: str, creator_role: str = "creator") -> str:
    return fingerprint(
        {
            "provider": str(provider).casefold(),
            "stable_creator_id": str(stable_creator_id),
            "creator_role": str(creator_role).casefold(),
        }
    )


def anchor_signal_key(provider: str, stable_creator_id: str, creator_role: str = "creator") -> str:
    return f"ml2:creator-anchor:{stable_identity_fingerprint(provider, stable_creator_id, creator_role)}"


def alias_signal_key(
    provider: str,
    stable_creator_id: str,
    alias_type: str,
    normalized_value: str,
    creator_role: str = "creator",
) -> str:
    return "ml2:creator-alias:" + fingerprint(
        {
            "identity": stable_identity_fingerprint(provider, stable_creator_id, creator_role),
            "alias_type": str(alias_type),
            "normalized_value": normalize_source_text(normalized_value),
        }
    )


def concept_key(provider: str, stable_creator_id: str, creator_role: str = "creator") -> str:
    return f"creator:{str(provider).casefold()}:{stable_identity_fingerprint(provider, stable_creator_id, creator_role)}"


def pair_id_for(left_signal_key: str, right_signal_key: str) -> str:
    left, right = sorted((str(left_signal_key), str(right_signal_key)))
    return fingerprint({"left_signal_key": left, "right_signal_key": right})


@dataclass(frozen=True)
class TrustedCreatorAlias:
    alias_type: str
    value: str
    canonical_key: str
    observation_refs: tuple[str, ...]
    parent_evidence_fingerprint: str


@dataclass(frozen=True)
class CreatorIdentityFamily:
    family_id: str
    provider: str
    stable_creator_id: str
    creator_role: str
    aliases: tuple[TrustedCreatorAlias, ...]
    metadata_refs: tuple[str, ...]
    work_context_distribution: Mapping[str, int]
    evidence_fingerprint: str
    existing_concept_id: int | None = None
    preexisting_active_concept_ids: tuple[int, ...] = ()

    @property
    def identity_fingerprint(self) -> str:
        return stable_identity_fingerprint(self.provider, self.stable_creator_id, self.creator_role)

    @property
    def anchor_key(self) -> str:
        return anchor_signal_key(self.provider, self.stable_creator_id, self.creator_role)


@dataclass(frozen=True)
class IdentityCandidate:
    pair_id: str
    family_id: str
    left_signal_key: str
    right_signal_key: str
    disposition: str
    reason_code: str
    evidence_fingerprint: str
    union_allowed: bool


def build_star_candidates(
    families: Sequence[CreatorIdentityFamily],
) -> tuple[IdentityCandidate, ...]:
    """Generate O(unique aliases) positive edges and collision-local negatives."""

    candidates: dict[str, IdentityCandidate] = {}
    alias_to_families: dict[str, list[CreatorIdentityFamily]] = defaultdict(list)
    for family in families:
        if not family.stable_creator_id or family.creator_role not in CREATOR_COMPATIBLE_ROLES:
            raise CreatorIdentityClosureError("invalid_creator_identity_family")
        seen_alias_signals: set[str] = set()
        for alias in family.aliases:
            alias_key = canonical_source_key(alias.value)
            if not alias_key:
                # Private provenance may retain the observation, but an empty
                # surface is not an identity/search candidate and cannot
                # create collision adjacency.
                continue
            signal_key = alias_signal_key(
                family.provider,
                family.stable_creator_id,
                alias.alias_type,
                alias.value,
                family.creator_role,
            )
            if signal_key in seen_alias_signals:
                continue
            seen_alias_signals.add(signal_key)
            pair_id = pair_id_for(family.anchor_key, signal_key)
            candidates[pair_id] = IdentityCandidate(
                pair_id=pair_id,
                family_id=family.family_id,
                left_signal_key=min(family.anchor_key, signal_key),
                right_signal_key=max(family.anchor_key, signal_key),
                disposition="must_link",
                reason_code="same_provider_stable_creator_id_trusted_parent",
                evidence_fingerprint=fingerprint(
                    {
                        "family": family.evidence_fingerprint,
                        "alias": alias.parent_evidence_fingerprint,
                    }
                ),
                union_allowed=True,
            )
            alias_to_families[alias_key].append(family)

    # A negative edge exists only when a real normalized alias surface creates
    # candidate adjacency.  We intentionally do not pair unrelated IDs.
    for alias_key, sharing in alias_to_families.items():
        unique = {family.identity_fingerprint: family for family in sharing}
        ordered = sorted(unique.values(), key=lambda value: value.identity_fingerprint)
        for index, left in enumerate(ordered):
            for right in ordered[index + 1 :]:
                if left.provider.casefold() != right.provider.casefold():
                    continue
                pair_id = pair_id_for(left.anchor_key, right.anchor_key)
                candidates[pair_id] = IdentityCandidate(
                    pair_id=pair_id,
                    family_id=left.family_id,
                    left_signal_key=min(left.anchor_key, right.anchor_key),
                    right_signal_key=max(left.anchor_key, right.anchor_key),
                    disposition="cannot_link",
                    reason_code="distinct_stable_creator_ids_shared_alias_surface",
                    evidence_fingerprint=fingerprint(
                        {
                            "alias_key": alias_key,
                            "left": left.identity_fingerprint,
                            "right": right.identity_fingerprint,
                        }
                    ),
                    union_allowed=False,
                )
    return tuple(sorted(candidates.values(), key=lambda value: value.pair_id))


def candidate_growth_accounting(
    families: Sequence[CreatorIdentityFamily], candidates: Sequence[IdentityCandidate]
) -> dict[str, Any]:
    unique_alias_signals = {
        alias_signal_key(
            family.provider,
            family.stable_creator_id,
            alias.alias_type,
            alias.value,
            family.creator_role,
        )
        for family in families
        for alias in family.aliases
        if canonical_source_key(alias.value)
    }
    positives = [row for row in candidates if row.disposition == "must_link"]
    negatives = [row for row in candidates if row.disposition == "cannot_link"]
    linear_bound = len(unique_alias_signals) + len(negatives)
    return {
        "family_count": len(families),
        "unique_alias_signal_count": len(unique_alias_signals),
        "must_link_candidate_count": len(positives),
        "collision_local_cannot_link_count": len(negatives),
        "candidate_pair_count": len(candidates),
        "linear_bound": linear_bound,
        "linear_bound_passed": len(positives) == len(unique_alias_signals) and len(candidates) <= linear_bound,
        "all_pairs_alias_expansion_used": False,
    }


def pair_accounting(
    candidates: Sequence[IdentityCandidate], dispositions: Iterable[Mapping[str, Any]] | None = None
) -> dict[str, Any]:
    rows = list(dispositions) if dispositions is not None else [
        {"pair_id": row.pair_id, "disposition": row.disposition} for row in candidates
    ]
    manifest_ids = {row.pair_id for row in candidates}
    ids = [str(row.get("pair_id") or "") for row in rows]
    counts = Counter(ids)
    disposition_counts = Counter(str(row.get("disposition") or "") for row in rows if row.get("pair_id") in manifest_ids)
    duplicates = sum(value - 1 for value in counts.values() if value > 1)
    missing = manifest_ids - set(ids)
    outside = set(ids) - manifest_ids
    invalid = sum(value for key, value in disposition_counts.items() if key not in PAIR_DISPOSITIONS)
    equality = len(manifest_ids) == sum(disposition_counts[key] for key in PAIR_DISPOSITIONS)
    return {
        "candidate_pair_count": len(manifest_ids),
        "must_link_count": disposition_counts["must_link"],
        "cannot_link_count": disposition_counts["cannot_link"],
        "deferred_nonblocking_count": disposition_counts["deferred_nonblocking"],
        "duplicate_pair_count": duplicates,
        "missing_pair_count": len(missing),
        "outside_manifest_pair_count": len(outside),
        "invalid_disposition_count": invalid,
        "accounting_equality_passed": equality and not duplicates and not missing and not outside and not invalid,
    }


def family_accounting(
    family_ids: Iterable[str], outcomes: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    expected = set(family_ids)
    rows = list(outcomes)
    counts = Counter(str(row.get("family_id") or "") for row in rows)
    outcome_counts = Counter(str(row.get("outcome") or "") for row in rows)
    valid_outcomes = {
        "already_materialized",
        "deterministic_must_link_materialized",
        "deterministic_cannot_link_preserved",
        "deferred_nonblocking_stable_identity_contradiction",
        "deferred_nonblocking_insufficient_trusted_alias_evidence",
        "deferred_nonblocking_existing_component_fragmentation",
    }
    duplicate = sum(value - 1 for value in counts.values() if value > 1)
    missing = expected - set(counts)
    outside = set(counts) - expected
    invalid = sum(value for key, value in outcome_counts.items() if key not in valid_outcomes)
    materialized = outcome_counts["already_materialized"] + outcome_counts["deterministic_must_link_materialized"]
    cannot_closed = outcome_counts["deterministic_cannot_link_preserved"]
    deferred = sum(value for key, value in outcome_counts.items() if key.startswith("deferred_nonblocking_"))
    equality = len(expected) == materialized + cannot_closed + deferred
    return {
        "identity_eligible_family_count": len(expected),
        "already_materialized_family_count": outcome_counts["already_materialized"],
        "newly_materialized_family_count": outcome_counts["deterministic_must_link_materialized"],
        "cannot_link_closed_family_count": cannot_closed,
        "deferred_nonblocking_family_count": deferred,
        "fragmented_deferred_family_count": outcome_counts[
            "deferred_nonblocking_existing_component_fragmentation"
        ],
        "duplicate_family_count": duplicate,
        "missing_family_count": len(missing),
        "outside_manifest_family_count": len(outside),
        "invalid_outcome_count": invalid,
        "accounting_equality_passed": equality and not duplicate and not missing and not outside and not invalid,
    }


def select_llm_manifest(
    candidates: Sequence[IdentityCandidate],
    accepted_pair_ids: Iterable[str],
    *,
    projected_cost_usd: float,
    cost_cap_usd: float = 10.0,
) -> tuple[IdentityCandidate, ...]:
    if projected_cost_usd > cost_cap_usd:
        raise CreatorIdentityClosureError("projected_llm_cost_exceeds_usd10")
    accepted = set(accepted_pair_ids)
    return tuple(
        row
        for row in candidates
        if row.pair_id not in accepted
        and row.disposition == "deferred_nonblocking"
        and row.reason_code == "requires_bounded_llm_adjudication"
    )


def component_purity(
    component_rows: Iterable[Mapping[str, Any]],
    cannot_pairs: Iterable[tuple[str, str]] = (),
) -> dict[str, Any]:
    rows = list(component_rows)
    by_component: dict[Any, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_component[row.get("component_id")].append(row)
    cannot = {tuple(sorted((str(left), str(right)))) for left, right in cannot_pairs}
    multi_stable = 0
    cross_role = 0
    unknown = 0
    direct_cannot = 0
    largest = 0
    distribution: Counter[int] = Counter()
    for values in by_component.values():
        keys = {str(row.get("stable_identity_key")) for row in values if row.get("stable_identity_key")}
        roles = {str(row.get("role") or "unknown") for row in values}
        signals = {str(row.get("signal_key") or "") for row in values}
        largest = max(largest, len(signals))
        distribution[len(signals)] += 1
        if len(keys) > 1:
            multi_stable += 1
        if any(role not in CREATOR_COMPATIBLE_ROLES for role in roles):
            unknown += int("unknown" in roles)
            cross_role += 1
        for left, right in cannot:
            if left in signals and right in signals:
                direct_cannot += 1
    return {
        "component_count": len(by_component),
        "component_size_distribution": {str(k): v for k, v in sorted(distribution.items())},
        "largest_component": largest,
        "multi_stable_id_creator_component_count": multi_stable,
        "unauthorized_cross_role_component_count": cross_role,
        "unknown_role_materialization_count": unknown,
        "direct_cannot_violation_count": direct_cannot,
        "transitive_cannot_violation_count": direct_cannot,
        "deferred_identity_union_count": 0,
    }


def audit_touched_identity_components(
    component_rows: Iterable[Mapping[str, Any]],
    cannot_pairs: Iterable[tuple[str, str]] = (),
    *,
    existing_concept_ids: Iterable[int] = (),
) -> dict[str, Any]:
    """Audit every active link on touched concepts, including legacy links.

    Components are the connected components of the signal/concept bipartite
    graph.  This distinguishes a direct cannot conflict (both endpoints link
    to one concept) from a transitive conflict (different concepts connected
    through another shared signal).
    """

    rows = [dict(row) for row in component_rows]
    concept_ids = {int(row["concept_id"]) for row in rows if row.get("concept_id") is not None}
    parent = {concept_id: concept_id for concept_id in concept_ids}

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: int, right: int) -> None:
        lroot, rroot = find(left), find(right)
        if lroot != rroot:
            parent[rroot] = lroot

    concepts_by_signal: dict[str, set[int]] = defaultdict(set)
    for row in rows:
        signal_key = str(row.get("signal_key") or "")
        concept_id = row.get("concept_id")
        if signal_key and concept_id is not None:
            concepts_by_signal[signal_key].add(int(concept_id))
    for values in concepts_by_signal.values():
        ordered = sorted(values)
        for concept_id in ordered[1:]:
            union(ordered[0], concept_id)

    component_by_concept = {concept_id: find(concept_id) for concept_id in concept_ids}
    rows_by_component: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        concept_id = row.get("concept_id")
        if concept_id is not None:
            rows_by_component[component_by_concept[int(concept_id)]].append(row)

    multi_stable = 0
    incompatible_role = 0
    unknown_role = 0
    character_work_contamination = 0
    trusted_parent_lineage_failure = 0
    distribution: Counter[int] = Counter()
    largest = 0
    for values in rows_by_component.values():
        signals = {str(row.get("signal_key") or "") for row in values if row.get("signal_key")}
        stable = {
            str(row.get("stable_identity_key"))
            for row in values
            if row.get("stable_identity_key")
        }
        roles = {str(row.get("role") or "unknown").casefold() for row in values}
        distribution[len(signals)] += 1
        largest = max(largest, len(signals))
        multi_stable += int(len(stable) > 1)
        unknown_role += int("unknown" in roles)
        bad_roles = roles - CREATOR_COMPATIBLE_ROLES
        incompatible_role += int(bool(bad_roles))
        character_work_contamination += int(
            bool(bad_roles & {"character", "work", "copyright"})
        )
        trusted_parent_lineage_failure += sum(
            1
            for row in values
            if row.get("source_kind") == "trusted_creator_alias"
            and not row.get("trusted_parent_lineage")
        )

    direct_cannot = 0
    transitive_cannot = 0
    cannot = {
        tuple(sorted((str(left), str(right))))
        for left, right in cannot_pairs
        if left and right and left != right
    }
    concepts_by_signal = {
        signal: set(values) for signal, values in concepts_by_signal.items()
    }
    for left, right in cannot:
        left_concepts = concepts_by_signal.get(left, set())
        right_concepts = concepts_by_signal.get(right, set())
        if left_concepts & right_concepts:
            direct_cannot += 1
            continue
        if any(
            component_by_concept.get(left_id) == component_by_concept.get(right_id)
            for left_id in left_concepts
            for right_id in right_concepts
        ):
            transitive_cannot += 1

    media_support_by_concept: dict[int, int] = defaultdict(int)
    identity_concepts: dict[str, set[int]] = defaultdict(set)
    for row in rows:
        concept_id = row.get("concept_id")
        if concept_id is None:
            continue
        concept_id = int(concept_id)
        media_support_by_concept[concept_id] = max(
            media_support_by_concept[concept_id],
            int(row.get("active_media_support_count") or 0),
        )
        stable_key = str(row.get("stable_identity_key") or "")
        if stable_key:
            identity_concepts[stable_key].add(concept_id)
    search_inert = sum(media_support_by_concept.get(concept_id, 0) == 0 for concept_id in concept_ids)
    duplicate_identity_concepts = sum(
        max(0, len(values) - 1) for values in identity_concepts.values()
    )

    existing_set = {int(value) for value in existing_concept_ids}
    existing_rows = [row for row in rows if int(row.get("concept_id") or -1) in existing_set]
    existing_signal_count = len(
        {str(row.get("signal_key")) for row in existing_rows if row.get("signal_key")}
    )
    existing_passed = bool(existing_set) and existing_set.issubset(concept_ids)
    existing_passed = bool(
        existing_passed
        and not multi_stable
        and not incompatible_role
        and not trusted_parent_lineage_failure
        and not direct_cannot
        and not transitive_cannot
        and all(media_support_by_concept.get(value, 0) > 0 for value in existing_set)
    )
    passed = not any(
        (
            multi_stable,
            incompatible_role,
            trusted_parent_lineage_failure,
            direct_cannot,
            transitive_cannot,
            search_inert,
            duplicate_identity_concepts,
        )
    )
    return {
        "full_touched_component_audit_passed": passed,
        "existing_12_full_component_audit_passed": existing_passed,
        "touched_active_concept_count": len(concept_ids),
        "component_count": len(rows_by_component),
        "component_size_distribution": {
            str(key): value for key, value in sorted(distribution.items())
        },
        "largest_component": largest,
        "full_touched_signal_count": len(concepts_by_signal),
        "existing_component_signal_count": existing_signal_count,
        "multi_stable_id_creator_component_count": multi_stable,
        "unauthorized_cross_role_component_count": incompatible_role,
        "unknown_role_materialization_count": unknown_role,
        "character_work_copyright_contamination_count": character_work_contamination,
        "trusted_parent_lineage_failure_count": trusted_parent_lineage_failure,
        "graph_audit_cannot_pair_count": len(cannot),
        "direct_disposition_conflict_count": direct_cannot,
        "cannot_endpoints_same_component_count": direct_cannot + transitive_cannot,
        "direct_cannot_violation_count": direct_cannot,
        "transitive_cannot_violation_count": transitive_cannot,
        "search_inert_materialized_concept_count": search_inert,
        "postclosure_duplicate_active_identity_concept_count": duplicate_identity_concepts,
    }
