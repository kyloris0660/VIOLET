# S2G-REAL1: Bounded Real AI Tagging Validation with DirectML

Status: `target_met_dry_run_only`.

Contract: `s2g_real1_bounded_ai_tagging_validation_contract_v1`.

Public summary: `docs/reports/s2g-real1-bounded-ai-tagging-validation-summary.json`.

## Scope

- Selection mode: `content_class_filter`.
- Selected media count: `3`.
- Max items: `3`.
- Public private locator values recorded: `False`.
- Full-library fallback: `False`.

## Dry-run Result

- Executed: `True`.
- Status: `completed`.
- Processed: `3`.
- Predicted tags: `32574`.
- Confirmed tag actions predicted: `121`.
- Suggestion actions predicted: `42`.
- Media tag delta: `0`.
- Dry-run media tag writes: `False`.

## Provider Result

- Requested provider preference: `['DmlExecutionProvider', 'CPUExecutionProvider']`.
- Actual provider: `DmlExecutionProvider`.
- Fallback occurred: `False`.
- Fallback reason: `None`.

## CPU Fallback

- Executed: `True`.
- Requested provider preference: `['CPUExecutionProvider']`.
- Actual provider: `CPUExecutionProvider`.
- Media tag delta: `0`.

## Write Result

- Executed: `False`.
- Status: `not_run_not_requested`.
- Exact operator confirmation present: `False`.
- Media tag delta: `0`.

## Load Control Observations

- Batch size: `2`.
- Effective batch size: `2`.
- CPU intra/inter threads: `4` / `1`.
- Preprocess workers: `2`.
- Max concurrent AI jobs: `1`.
- Appeared bounded: `True`.

## Safety

- Production S3A execution remains disabled.
- Unattended S3B remains disabled.
- Provider/Pixiv/gallery-dl/SauceNAO/Google/R1R/Entity operations were not run.
- Cleanup/delete/reset/drop/truncate was not run.
- Reports are aggregate and path-redacted.

## Backlog

- DirectML package scope scanner perfection remains deferred.
- Smoke runner app.config side-effect perfection remains deferred.
- Fallback field naming cleanup remains deferred unless it blocks a contract.
- Durable per-job provider provenance schema migration remains a later reviewed phase.
