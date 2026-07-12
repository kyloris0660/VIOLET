# SourceConcept Tag-Search Semantics

## Purpose

This policy separates source-layer identity resolution from media retrieval.
Search reads evidence and returns media; it never changes `SourceConcept`
membership, creates Entity truth, or authorizes a new identity relation.

## Identity Union

Identity union places two source signals in one materialized `SourceConcept`.
It requires approved identity evidence and all deterministic/component guards.
`cannot_link`, deferred evidence, rejected evidence, weak search-only evidence,
role conflicts, and hard work-context conflicts prevent identity union.

## Search-Result Union

Search-result union combines media sets that independently satisfy one query
term. It is not identity union. If distinct concepts, roles, or works legitimately
carry the same observed name, a bare-name query returns all supported media even
when those identities are `cannot_link`.

## Supported Evidence

A returned media item must have a trace to at least one approved search support:

- direct observed source name or tag;
- direct application tag;
- accepted materialized alias;
- accepted search-only alias relation that preserves role/context and does not
  assert identity;
- exact creator/source metadata;
- another explicitly versioned and approved source-layer evidence type.

Rejected, superseded, invalid, deleted, or otherwise non-query-visible evidence
must not produce results. Direct evidence belongs to the media that carries it;
a `cannot_link` relation elsewhere must not globally erase that direct match.

## Accepted Alias Propagation

Alias propagation may expand a query only when the target media has direct or
accepted alias evidence with provenance. A `cannot_link` guard blocks propagation
that would falsely assert or traverse identity; it does not suppress direct
same-key observations on either side. Search-only equivalence remains
non-materialized and must not mutate identity components.

## AND Intersection

Every positive query term is an independent media constraint. The runtime result
is the intersection of the per-term supported media sets. For example, a shared
`temp001` query may return several works, while `temp001 work_a` returns only
media satisfying both terms. Negated terms exclude their supported media sets.

## Shared Names And Collisions

Shared names are expected. Artist/creator, character, work, and other roles may
use the same surface string. Bare-name search may return all supported roles;
their SourceConcept identities remain separate. Role, work, source, and other
tag terms narrow the media results through AND intersection.

## Role, Work, And Source Constraints

An explicit role/work/source constraint must be enforced, not treated as a hint.
No result may leak when it fails any positive term. The support trace must record
the matched evidence type and retain its role, work context, source record, and
provenance when those dimensions exist.

## Multilingual Alias Equivalence

Observed, registered, or deterministically generated in-scope multilingual
aliases must be accounted. Strong source-confirmed same-entity aliases may be
materialized when identity guards pass; otherwise they may be search-equivalent
without identity union. Generated transliteration alone is candidate/search
evidence, not automatic identity truth. No alias may silently disappear.

## Creator And Artist Search

A stable provider creator ID is the source-layer identity anchor when available.
Display names and account/handle values are mutable observations attached to that
anchor; all observed values are retained. Bare creator-name search returns every
local media item supported by exact creator metadata, including separate creators
sharing a display name. Creator-name/account plus character/work terms intersect
at media level. Creator evidence remains source-layer evidence, not Entity truth.

## Invalid Results

A result is invalid when its queried term lacks legitimate support, rejected or
superseded evidence is exposed, an AND/role/work/source constraint is ignored,
alias propagation reaches an unsupported target, or search changes/implies
identity membership. Multiple identities in a supported bare-name result are not
by themselves invalid.
