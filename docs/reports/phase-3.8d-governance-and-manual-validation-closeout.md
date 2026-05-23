# Phase 3.8d-G1 Governance and Manual Validation Workflow Closeout

## Summary

Phase 3.8d-I7 has been merged and manually validated. The medium pilot pipeline is accepted at a practical level:

- `994` staged-success rows were imported under the I7 source label.
- The `6` I6 failed rows (`799`, `839`, `922`, `970`, `971`, `972`) remained excluded/deferred.
- Classification-first pipeline completed.
- AI tagging ran only for anime/unknown rows.
- Eligible general/meta localization completed with `0` remaining missing general/meta translations.
- Manual validation found no major issues.
- A few odd LLM translations are accepted as non-blocking and should be handled later by proper-noun/entity/character localization strategy.

This G1 stage is documentation/governance only. It does not change runtime behavior and does not add a one-off validation script.

## Governance Updates

Updated `AGENTS.md`, `CLAUDE.md`, and `docs/test-workflow.md` with:

- `Artifact and Operational Script Lifecycle Policy`
- `Reviewer Feedback and Artifact Lifecycle Rule`
- Strengthened `Engineering judgment / operator notes` requirements

The lifecycle policy requires new scripts/tools/reports to declare whether they are:

- production reusable code
- reusable validation/safety tools
- phase-scoped operational runners
- one-off local artifacts / temporary validation outputs
- public reports / handoff / roadmap

The reviewer lifecycle rule states that current phase correctness, mutation safety, item ledger truthfulness, privacy/public report safety, data integrity, failure/success classification, and safe continuation findings must be fixed even for phase-scoped runners. Future-reuse/generalization/polish findings may be deferred when they do not affect current phase safety or decision-making.

## Manual Validation Workflow

Added `docs/manual-validation.md` and linked it from `docs/test-workflow.md` and `docs/current-handoff.md`.

The documented development/blombooru manual validation flow includes:

- when to use development manual validation
- when not to load `. "$env:USERPROFILE\.violet\test-env.ps1"`
- current `PYTHONPATH=<repo>\backend` startup requirement
- Terminal A server startup commands
- Terminal B read-only validation commands
- API/admin/browser smoke checks
- source-label DB check pattern
- must-check items
- stop-and-report conditions
- server stop and port-release confirmation
- explicit instruction not to create one-off `manual_i7_validation_preflight.py`

## Startup Path Finding

Current manual validation requires `PYTHONPATH=<repo>\backend` because `run.py` loads `backend.app.main:app` while some modules still use `app.*` imports, including `backend/app/services/source_ingestion_gate.py`.

This is recorded as an accepted current manual validation requirement and as a future import/startup path consistency hardening item. It is not fixed in this docs-only G1 stage.

## Roadmap Updates

Updated `docs/project-roadmap.md` with:

- Phase 3.8d-I7 manual validation acceptance note
- Phase 3.8d-G1 governance/manual validation closeout scope
- high-priority future prerequisite: production Ingestion Run Ledger / Source Item State Ledger before full-library import
- high-priority future prerequisite: over-selection buffer (`desired_success_count=N`, `candidate_count=N * buffer_ratio`)
- medium-priority import/startup path consistency hardening
- medium-priority proper-noun/entity/character localization strategy
- lower-priority admin stats/settings/information architecture/management UX debt

## Artifact Lifecycle

- `docs/manual-validation.md`: public durable workflow documentation.
- `docs/reports/phase-3.8d-governance-and-manual-validation-closeout.md`: public closeout report.
- `docs/reports/phase-3.8d-governance-and-manual-validation-closeout-summary.json`: public machine-readable closeout summary.
- No production reusable code added.
- No reusable validation/safety tool added.
- No phase-scoped operational runner added.
- No one-off local validation script added.

## Safety Confirmation

- No DB import.
- No classification.
- No AI tagging.
- No localization.
- No staging copy.
- No source/iCloud mutation.
- No app-managed storage mutation.
- No cleanup/delete/reset/drop/truncate.
- No Entity Resolver.
- No similarity/clustering.
- No Phase 4 work.
- No runtime server startup for this G1 stage.
- No push to `main`.
- No merge.

## Engineering Judgment / Operator Notes

- **Phase boundary:** Appropriate. This stage is intentionally docs/governance only and should not be expanded into runtime hardening or new validation tooling.
- **Risk assessment:** The main remaining operational risk is startup/import-path fragility. It is documented and should be fixed later as a bounded import/startup consistency hardening phase. It is not a blocker for the accepted I7 result.
- **Reviewer feedback assessment:** This stage adds lifecycle guidance so future reviewer findings are judged by whether they affect current safety/truthfulness versus future reuse/generalization.
- **Artifact lifecycle assessment:** The new artifacts are public durable documentation and reports. No code or scripts were added.
- **Prompt critique:** The prompt correctly avoided runtime hardening and one-off validation scripts. That boundary should be preserved.
- **Next-step recommendation:** Treat Phase 3.8d medium pilot as accepted at the practical level, then choose a next phase explicitly: manual QA / Phase 3.8d closeout, import/startup path consistency hardening, production ingestion ledger design, or proper-noun/entity localization strategy. Do not start Phase 4 until the full-library ingestion prerequisites are planned and approved.
