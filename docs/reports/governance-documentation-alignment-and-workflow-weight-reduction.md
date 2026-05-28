# Governance Documentation Alignment and Workflow Weight Reduction

## Why This Stage Exists

Recent phases proved that reliability controls are necessary, but the workflow had become too heavy: reviewer loops, phase-scoped validation runners, preflight scripts, and governance docs were consuming development time out of proportion to the risk of some artifacts.

GOV-2 persists the new project-level decision: reliability remains high, while workflow weight decreases. The main correction is to distinguish durable core architecture from phase-scoped or one-off tooling, and to evaluate reviewer findings by lifecycle plus current-stage impact rather than severity label alone.

This stage updates active guidance docs only. Historical phase reports are left archival and are not rewritten.

## New Governance Policy Summary

Reliability remains mandatory for:

- DB schema and migrations.
- Provider-neutral evidence contracts.
- Entity / Alias / Evidence / Candidate / Assignment lifecycle.
- `ProviderCache`, `EntityEvidence`, `MediaEntityCandidate`, and `NegativeLookupCache` write semantics.
- External provider uploads, privacy gates, budgets, cache/audit/rate-limit design, and separate run approval.
- Confirmed assignment policy: manual confirmation or explicitly policy-approved only.
- Source/iCloud/app-managed storage mutation safety.
- Broad or repeated provider runs, which require run ledger discipline.
- E2E delivery when E2E is in scope: 0 failures required, skipped tests explicitly gated.

Workflow weight must decrease by preferring executable guards, assertions, DB constraints, transaction boundaries, enum states, allowlists/denylists, and focused tests over long prompt-only constraints, repeated docs-only gates, overly fragmented phases, or generic frameworks for one-off scripts.

Every new script/tool/report/artifact must be classified as:

1. Durable production code.
2. Reusable validation/safety tool.
3. Phase-scoped operational runner.
4. One-off local artifact / ignored output.
5. Public report / handoff / roadmap update.

Default reviewer closeout is 1-2 bounded fix rounds per PR. Continue beyond that only for current-stage data corruption, DB writes executed by the PR, privacy leaks, provider upload safety, current-stage report truthfulness, confirmed assignment/media_tags/entity truth pollution, core contract/schema correctness consumed by the PR, or irreversible operation safety. Otherwise record as deferred and move it into the phase where it matters.

Severity label alone is not enough. P1/P2 is a signal, not an automatic merge block.

## Document Audit Table

| File | Stale/conflicting guidance found | Action taken | Reason | Risk if left unchanged |
|------|----------------------------------|--------------|--------|------------------------|
| `README.md` | Overlong project entry page, stale feature status, corrupted user-facing text, old reverse-search status. | Rewritten. | Keep README concise and point future agents to active handoff/roadmap/manual-validation docs. | Future agents might start from stale status or misread the canonical repo/current route. |
| `AGENTS.md` | Reviewer/artifact policy existed but lacked GOV-2 bounded closeout, severity-vs-impact rule, phase transfer rule, prompt requirements, and workflow weight reduction principle. Test checklist implied full non-E2E as a default gate. | Rewritten sections. | Make the agent instruction source of truth match current policy. | Automated agents could keep treating all P1/P2 or future-generalization comments as blockers. |
| `CLAUDE.md` | Same GOV-2 gaps as `AGENTS.md`; checklist implied full non-E2E as a default gate. | Rewritten sections. | Keep Claude/agent guidance consistent. | Parallel guidance files could conflict and pull future work back into over-closeout loops. |
| `CONTRIBUTING.md` | Blanket `pytest tests/` guidance did not distinguish docs-only, phase-scoped, reusable tooling, and durable runtime changes. | Rewritten. | Make public contribution guidance lifecycle-aware. | Contributors could over-test docs-only changes or under-classify new tools. |
| `SECURITY.md` | Security page did not explicitly reflect current provider-upload privacy policy or low-confidence provider-result trust boundary. | Rewritten/addition. | Align security/privacy docs with provider route policy. | Future provider phases might miss upload/privacy and weak-evidence constraints. |
| `docs/current-handoff.md` | Bloated historical detail, stale PR #79 "active" state, many old blocked snapshots, and old workflow framing. | Rewritten concise handoff. | Make handoff current after PR #79 and GOV-2. Preserve important PR traceability without duplicating all reports. | New agents could follow obsolete blocked language or old phase split momentum. |
| `docs/project-roadmap.md` | Needed GOV-2 phase/granularity policy, bounded reviewer closeout, and updated C1/D0/B2/3.9 route. | Rewritten/additions. | Roadmap should make product/data-model progress the default mechanism, not process proliferation. | Future work could keep adding R/G/S/I docs phases without real value. |
| `docs/test-workflow.md` | Top of file duplicated general governance and implied testing policy should carry all reviewer/artifact rules. Needed scope-based validation guidance. | Rewritten as scope-based validation policy. | Keep test workflow focused on validation selection and server identity gates. | Docs-only and phase-scoped PRs could be forced through full runtime suites without risk basis. |
| `docs/manual-validation.md` | Superseded `PYTHONPATH=<repo>\backend` workaround was shown as a code block; expected branch was hardcoded to `main`. | Rewritten. | Keep manual validation practical and accurate for PR branches after G2. | Operators could preserve stale startup workarounds or validate the wrong branch. |
| `docs/icloud-safe-ingestion.md` | Source/iCloud gate wording could be overgeneralized to provider workflows that use app-managed derived inputs. | Rewritten/addition. | Preserve real source safety while clarifying provider privacy/upload gates are separate. | Future provider work could incorrectly inherit path-source cloud gates or skip provider privacy gates. |
| Other active docs under `docs/` outside `docs/reports/` | Searched for reviewer/P1/P2/phase-scoped/PYTHONPATH/SauceNAO/exact-source/manual-review conflicts. Domain docs mostly contained feature-specific historical or technical facts. | Kept. | Avoid rewriting domain docs that are not current governance sources. | Low, because current handoff/roadmap now say not to infer current status from older docs. |
| Historical `docs/reports/**` | Reports contain old phase facts and old stop states. | Kept archival. | User explicitly forbade rewriting historical phase reports except adding this GOV-2 report. | Low if future agents follow handoff/roadmap first; old reports remain evidence, not current policy. |

## Old Policies Removed or Rewritten

- Replaced implied "fix all serious reviewer feedback" with lifecycle plus current-stage impact.
- Replaced "P1/P2 always block" with "P1/P2 is a signal, not an automatic decision."
- Replaced unbounded reviewer closeout with a default 1-2 bounded fix rounds per PR.
- Replaced "phase-scoped runners should be reviewed like durable frameworks" with lifecycle-specific review.
- Replaced blanket full non-E2E expectations for all PRs with scope-based validation.
- Clarified no-active-server preflight is mandatory before agent-started/manual-validation servers, not every docs-only task.
- Replaced stale "PR #79 active" handoff state with merged PR #79 state.
- Replaced exact-source-inventory-first assumptions with no-source source-discovery route and provider-neutral contract route.
- Clarified high-confidence SauceNAO results are viable evidence candidates, while low-confidence SauceNAO results are discarded by default.
- Clarified SauceNAO character metadata can exist in high-confidence API data and should be preserved as source-backed candidate metadata, not final truth.
- Clarified old `PYTHONPATH=<repo>\backend` startup workaround is historical, not current workflow.

## Remaining Known Deferred Governance Issues

- Some older domain docs still contain historical phase-specific language and mojibake in archival or low-priority sections. This PR fixes active governance sources where they affect current workflow, but does not perform a broad encoding cleanup.
- Historical reports remain unchanged by design. They may contain old blocked states or old workflow assumptions; the handoff and roadmap now instruct agents not to treat them as current.
- The PR body format still uses the established V.I.O.L.E.T. structure. GOV-2 only changes checklist semantics so non-applicable gates are explicit rather than forced.
- Future prompts still need to apply GOV-2 actively by naming artifact lifecycle, must-fix/deferable reviewer categories, DB/provider scope, non-goals, and expected reviewer closeout bound.

## Updated Future Roadmap Summary

Near-term route after PR #79:

1. Phase 4.4-C1: persist only validated high-confidence evidence for `2687` and `2670` through the provider-neutral contract. No confirmed assignments, automatic Entity creation, media_tags mutation, TagTranslation mutation, localization execution, provider rerun, upload, or broad sample expansion unless separately approved.
2. Phase 4.4-D0: scout a second provider against the same contract without a separate DB write path.
3. Phase 4.4-B2 only if more sample evidence is needed: `20-30` explicit user-approved anime samples, one provider, quota-aware scheduling, no originals, no full-library selection, and no DB writes unless separately approved.
4. Phase 3.9 before broad/repeated provider runs, `100+` scale, 5k/10k scale, large cache population, full-library scheduling, or full-library import.

## Safety Confirmation

- No runtime code change.
- No provider call.
- No upload.
- No DB write.
- No DB migration.
- No source/iCloud mutation.
- No app-managed storage mutation.
- No Entity Resolver execution.
- No similarity/clustering.
- No localization execution.
- No C1 persistence.
- No push to `main`.
- No merge.
