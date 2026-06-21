# S2G/S3A Integration Decision

Status: S2G-1X decision after the GPU AI tagging capability probe.

Public evidence:

- `docs/reports/s2g1x-gpu-ai-tagging-probe.md`
- `docs/reports/s2g1x-gpu-ai-tagging-probe-summary.json`
- Contract: `s2g1x_probe_contract_v1`

## Decision

S2G and S3A should share one job/progress/throttle/ledger foundation, but they
should not share one production execution phase yet.

The safe implementation boundary is:

```text
S2G/S3A shared foundation first
-> S2G provider/load-control implementation and validation
-> separately approved production S3A execution
-> later opt-in S3B automation
```

Production S3A execution remains disabled until a separate operator-approved
phase. Unattended S3B automation remains out of scope.

## Evidence

The S2G-1X probe found:

- Current app WD tagging code forces `CPUExecutionProvider`.
- The configured model is `wd-swinv2-tagger-v3`.
- The local model cache is present; no network model download was performed.
- ONNX Runtime on this Windows setup exposes `CPUExecutionProvider`, but not
  CUDA or DirectML providers.
- CPU fallback loaded the WD model and completed the tiny synthetic benchmark.
- Current risk flags include all-CPU-thread default session options, no durable
  pause/resume, and missing persisted provider/backend provenance.

This means S3A should not chain production AI tagging until S2G adds explicit
provider selection, conservative load-control defaults, provenance, and shared
failure/ledger semantics.

## Code Paths To Reuse

S2G should reuse and harden:

- `backend/app/services/wd_tagger.py` for WD model inference, after adding an
  execution-provider abstraction and bounded session options.
- `backend/app/services/ai_tagging_service.py` for tag prediction thresholding
  and tag-service writes, after adding provider/model/backend provenance.
- `backend/app/services/ai_tagging_job_service.py` for single-job concurrency,
  cancel support, progress flush, and localization scheduling, after promoting
  pause/resume and failure-budget semantics.

S3A should reuse and harden:

- `backend/app/services/dynamic_library_sync_service.py` for source-root
  identity, update checks, item state, pending counts, and safe dry-run state.
- `DynamicSourceRoot`, `DynamicSourceItem`, `DynamicSyncRun`, and
  `DynamicSyncRunItem` as the source-side ledger foundation.
- Existing classification, AI tagging, and localization readiness checks, but
  only through a shared job runner after production promotion gates pass.

## Execution Boundary

The shared execution boundary should be a background job runner with:

- Admin API manual trigger for operator-driven runs.
- CLI probe/validation entry points for pre-production and CI-style checks.
- Durable run, stage, and item progress snapshots.
- Single active production job by default.
- Explicit load-control config per job.
- Failure budgets and retry policy per stage.
- Redacted public summaries and private ignored ledgers.

Large update checks and S3A execution should not stay inside a long-running
FastAPI request handler. The Admin API should submit and observe jobs; workers
should execute bounded stages.

## Reusable Job Model Needed

Both S2G and S3A need these shared concepts:

- `JobRun`: manual trigger, dry-run/production mode, status, timestamps,
  operator approval evidence, and promotion gate status.
- `StageRun`: ordered stage name, status, processed/failed/skipped counts,
  retry budget, failure budget, and stop reason.
- `ItemLedger`: source item/media id link, safe public label, stage status,
  failure/deferred reason, retry state, bytes copied where applicable, and
  imported media id when later available.
- `LoadControlConfig`: provider preference, batch size, worker count,
  concurrent jobs, preprocessing workers, CPU threads, priority, fallback, and
  pause/resume capability.
- `ProviderCapability`: model identity, provider availability, loaded backend,
  benchmark status, and public-safe throughput.
- `ProgressSnapshot`: pollable summary for Admin UI and CLI.

S2G-1X implemented a stdlib-only scaffold for these concepts in
`scripts/s2g_s3a_job_control.py`. It is a reusable validation/safety tool, not
yet the production DB schema.

## Implemented Now

S2G-1X safely implemented:

- A local-only AI tagging provider capability probe.
- A public probe report and JSON summary.
- A narrow executable contract for safe probe and integration-decision claims.
- A stdlib-only load-control and S3A dry-run plan scaffold.
- Focused tests for the scaffold, probe summary, and contract.

No production writes, model downloads, provider calls, S3A execution, or S3B
automation were enabled.

## Must Wait

These require separate operator-approved phases:

- Installing or switching ONNX Runtime CUDA/DirectML packages.
- Runtime provider abstraction inside `WDTagger`.
- DB schema changes for durable shared job/run/stage/item ledgers.
- Persisted provider/backend/model/batch provenance for AI tag results.
- Production S3A execution.
- Admin UI controls for pause/resume, throttles, and retry.
- Any production import, classification, AI tagging, localization, or source
  hydration/read workflow.

## Recommended Next Phase

Proceed to a combined S2G/S3A foundation phase, not immediate production S3A.

The next phase should promote the scaffold into a reviewed runtime foundation
only as far as needed for safe provider/load-control execution:

1. Add explicit provider selection and bounded CPU thread defaults to WD tagging.
2. Add provider/model/backend/load-control provenance to AI tagging job outputs.
3. Add durable job/stage/item planning models or migrations only after a
   reviewed schema plan.
4. Keep production S3A execution disabled until S2G load-control is validated.
