# ADR-0001: Stable Replay Evidence v2

- Status: Accepted for SCV2-SV1B recovery
- Date: 2026-07-25
- Owner decision: `owner_authorized_fresh_replay_v2_20260725`

## Context

The SV1B v1 stable package used a recursive field-name sanitizer. It removed
`provenance.stable_identity_key.work_id`, even though that value is a
graph-effective provider identity rather than a development database row ID.
Both sides of the old package equality check used the same lossy projection, so
the comparison could not detect the common-mode loss. The failed retry2 Replay
is retained unchanged as forensic evidence.

## Decision

1. Stable replay packages are versioned and reject missing, malformed, or
   unsupported `schema_version` values.
2. Serialization is schema-aware. Stable fields are defined by an explicit
   record schema and JSON path; field names alone do not imply preservation.
3. Provider work, creator/account, and page identities are retained only at
   their declared schema paths.
4. Development numeric row IDs are not cross-database identities. They must be
   rejected or converted to a stable source reference before export.
5. `compatible_complete_record_reuse` carries a stable source-record key,
   provider key, or stable fingerprint; it never carries a
   `source_metadata_record_id` numeric row ID across databases.
6. Unknown, removed, or unmapped fields enter a field-level preservation/loss
   ledger. Loss of a graph-effective or trusted-predicate dependency fails
   closed.
7. Primary is not authoritative merely because it is Primary. Provider stable
   identity must be cross-checked against accepted acquisition checkpoints,
   request/outcome ledgers, persisted provider evidence, or another immutable
   accepted source.
8. The failed retry2 Replay remains immutable. Recovery creates one fresh,
   isolated Replay verification database from accepted evidence.
9. Long-term acceptance requires:
   `Primary export -> fresh import -> Replay re-export -> exact package equality`,
   graph-effective projection equality, and trusted-complete verdict/count
   equality.
10. Stable package v1 and its fingerprints remain historical evidence and are
    never rewritten in place.

## Rejected Alternatives

- Extending a global `*_id` deletion rule with an ever-growing field-name
  allowlist: the same field name has different semantics under different
  schemas and paths.
- Repairing 13,261 failed Replay provenance rows in place: it would destroy the
  failed-state forensic checkpoint and blur the import boundary.
- Copying current Primary identity without acquisition-evidence corroboration:
  it would turn database position into unsupported truth.

## Consequences

The v2 exporter/importer is slightly stricter and must maintain a loss ledger,
but silent graph-effective loss becomes an executable failure. The one fresh
Replay is disposable test evidence; the accepted v1 inputs and failed retry2
Replay stay protected.

## Links

- [Current phase state](../state/current-phase.json)
- [Replay provenance incident](../reports/phase-4.5-scv2-sv1b-replay-trusted-provenance-checkpoint.md)
- [Phase contracts](../phase-contracts.md)
