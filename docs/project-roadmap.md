# V.I.O.L.E.T. Project Roadmap

## Project Vision

V.I.O.L.E.T. is a personal, local-first anime and illustration library. It
combines safe ingestion, local classification and AI tagging, Danbooru-style
retrieval, Chinese display localization, and provenance-preserving source
evidence without treating weak AI or provider signals as user truth.

## Current Active Roadmap

<!-- CURRENT_PHASE: SCV2-FL1-P1-R1 -->

The authoritative current state is `docs/state/current-phase.json`.

`SCV2-FL1-P1-R1: Late Review Safety Remediation And Authority Correction` is
the current phase on Draft PR #143.
PR #139 / SCV2-SV1B merged into `origin/main` at
`33af4111e1595dac3ece0ac50002556d466f0138`. Its final owner acceptance remains
`37 PASS`, `3 owner-waived nonblocking known limitations`, `0 PENDING`, and
`0 unwaived FAIL`; the B01/B04/B08 waiver is historical SV1B evidence and is
not an FL1 scale-up waiver.

PR #140 merged the owner-approved FL1 plan into `origin/main` at
`9ce1128be643c0eaa998ccdff8890d76196ce7db`. P1 implements only explicit
Dev/Test identity and containment, default-deny synthetic mutation, the
registered FL1 contract, stable item identity, and restartable per-item ledger
foundations. It authorizes no production access, existing database access, real
source-root access or inventory, import, classification, AI tagging,
provider/LLM/media request, Stable Replay, localization, graph/search
derivation, or Entity/truth promotion.

PR #141 physically merged at
`36100bfa0317387e064cd87b2e753eca3a201b5e`, but eight valid review findings
arrived after merge and remain unresolved on that historical PR. P1-R1 is the
only authorized remediation route. Its implementation evidence is
`8fc1df2484f2a3de12639cf43e0e2c38131239b5`, pending independent owner audit.
Consequently `target_met=false`, `safe_to_merge=false`,
`route_approved=false`, and `next_phase_started=false`.

PR #142 is a non-authoritative candidate because it mixed P1 remediation with
unapproved FL1-I1 work. It does not establish acceptance, merge authority, or
an I1 route. FL1-I1 remains a future planning candidate that requires both the
P1-R1 owner audit and a separate owner scope decision.

The current bounded PR #143 review-fix round keeps the same P1-R1 scope. It
hardens repository evidence for both PR audit and squash carry-forward,
requires an independent five-scenario failure-budget/manual-stop matrix,
reconciles synthetic invocations per item, and treats phase-level zero-activity
statements only as non-action attestations. Executable operation evidence still
comes exclusively from the instrumented private `RunLedger`.

The second bounded review-fix round addresses four findings from review
`4888894624` on reviewed HEAD
`389ba79cdf9cdd06f81fba07889f21245fd44072`; three are inline and the fourth
public-summary redaction P1 is in the review submission body. Audit-ready stage
claims now require trusted repository, main runtime ledger, private
failure-budget scenario, and private interrupted-reconciliation evidence. The
complete public summary is recursively redaction-scanned; editable booleans or
digests cannot substitute for that scan.

## Accepted Mainline Sequence

1. R1R / PR #132.
2. SCV2-A1R / PR #133.
3. SCV2-R2 / PR #134.
4. SCV2-R2R / PR #135.
5. SCV2-ML1 / PR #136.
6. SCV2-ML2 / PR #137.
7. SCV2-SV1-A / PR #138.
8. SCV2-SV1B / PR #139, squash-merged at
   `33af4111e1595dac3ece0ac50002556d466f0138`.
9. SCV2-FL1 planning / PR #140, squash-merged at
   `9ce1128be643c0eaa998ccdff8890d76196ce7db`.
10. SCV2-FL1-P1 / PR #141, physically merged at
    `36100bfa0317387e064cd87b2e753eca3a201b5e`; late-review remediation is not
    yet owner-accepted.

Exact phase evidence and limitations remain in the accepted reports. This
roadmap intentionally does not duplicate their execution accounting.

## Proposed FL1 Route

1. Complete P1-R1 Draft PR #143 and stop at independent owner audit.
2. Obtain separate owner acceptance and merge authorization if the owner judges
   the remediation sufficient.
3. Make a separate FL1-I1 scope decision; no I1 implementation has started.
4. Stop before any real source scan for exact source-scope authorization.
5. If separately approved, execute only bounded Dev/Test import and local
   classification/AI-tagging stages under exact mutation allowlists and failure
   budgets.
6. Require a bound manual-acceptance sample before any production planning.

Production remains a later, independent route. The historical
`Phase 4.6-FULLLIB-P0 Production Full-Library Import and AI Tagging Plan` is a
design input only and is not current authorization.

## Durable Boundaries

- Source/iCloud roots remain inaccessible and out of scope during FL1-P1.
- Dev/Test databases and storage must be isolated from production and from
  accepted SV1 evidence databases.
- AI proper-noun signals remain weak evidence, never automatic Entity truth.
- Provider or external calls require provider-specific privacy, budget, cache,
  rate-limit, and separate run authorization.
- No full-library or production route may inherit the SV1B placeholder-creator
  limitation waiver.
- Manual acceptance and browser prevalidation are separate gates.

## Documentation Map

- Current route: `docs/state/current-phase.json`
- Generated handoff: `docs/current-handoff.md`
- Current mainline detail: `docs/roadmap/current-mainline-roadmap.md`
- FL1 plan: `docs/plans/phase-4.6-scv2-fl1-isolated-full-library-dev-test-plan.md`
- Executable contract boundary: `docs/phase-contracts.md`
- Historical roadmap through SV1B:
  `docs/roadmap/archive/project-roadmap-through-scv2-sv1b.md`
- Detailed agent runbook: `docs/development/agent-runbook.md`

## Governance

Current facts must change in `docs/state/current-phase.json` first. Active
roadmaps and the generated handoff must agree with its single phase marker.
Historical reports and archives preserve their captured semantics; they do not
become current merely because they remain linked.
