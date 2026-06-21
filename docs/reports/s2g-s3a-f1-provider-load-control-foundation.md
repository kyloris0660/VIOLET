# S2G/S3A-F1: WDTagger Provider and Load-Control Foundation

Status: target_met.

Contract: `s2g_s3a_f1_foundation_contract_v1`.

Public summary: `docs/reports/s2g-s3a-f1-provider-load-control-foundation-summary.json`.

## Summary

This phase implements the first runtime foundation shared by S2G and future
S3A without enabling production S3A execution.

Implemented now:

- WDTagger provider abstraction for `CUDAExecutionProvider`,
  `DmlExecutionProvider`, and `CPUExecutionProvider`.
- Truthful provider fallback reporting.
- Bounded CPU load-control defaults.
- Provider/model/backend provenance in AI tagging runtime outputs.
- Shared job/progress/load/provider vocabulary in
  `backend/app/services/job_control.py`.
- Dry-run-only S3A stage planning vocabulary.

## Provider Behavior

Requested provider preference:

```text
CUDAExecutionProvider,DmlExecutionProvider,CPUExecutionProvider
```

ONNX Runtime providers available in the smoke validation:

```text
AzureExecutionProvider,CPUExecutionProvider
```

Actual loaded provider:

```text
CPUExecutionProvider
```

Fallback occurred because the requested CUDA and DirectML providers are not
available in the current ONNX Runtime environment. The runtime records this as:

```text
unavailable_requested_providers=CUDAExecutionProvider,DmlExecutionProvider
```

This phase does not install CUDA, DirectML, or ONNX Runtime packages, and it
does not claim GPU usage when the runtime falls back to CPU.

## Load Control

Conservative defaults now available through settings/env:

| Setting | Effective default |
| --- | --- |
| `AI_TAGGING_PROVIDER_PREFERENCE` | `CUDAExecutionProvider,DmlExecutionProvider,CPUExecutionProvider` |
| `AI_TAGGING_CPU_INTRA_OP_THREADS` | `4` |
| `AI_TAGGING_CPU_INTER_OP_THREADS` | `1` |
| `AI_TAGGING_PREPROCESS_WORKERS` | `2` |
| `AI_TAGGING_EXECUTION_MODE` | `ORT_SEQUENTIAL` |
| `AI_TAGGING_PROCESS_PRIORITY` | `below_normal` |
| `AI_TAGGING_BATCH_SIZE` | `2` |
| `AI_TAGGING_MAX_CONCURRENT_JOBS` | `1` |

`AI_TAGGING_PROCESS_PRIORITY` is recorded but not applied to the shared FastAPI
process in this phase. The active controls are bounded ONNX session threads,
bounded preprocessing, bounded batch size, and single active AI job semantics.

## Provenance

AI tagging runtime summaries now expose:

- model name;
- model repo id;
- thresholds;
- requested provider preference;
- actual ONNX provider loaded;
- fallback reason when fallback occurs;
- batch size;
- CPU intra/inter thread settings;
- preprocess workers;
- execution mode;
- tagger version/source;
- backend.

No DB schema migration was added. Durable DB persistence of this provenance
should be planned separately if a later production phase requires it.

## Shared Foundation

Runtime shared vocabulary now lives in:

```text
backend/app/services/job_control.py
```

Concepts provided:

- `LoadControlConfig`
- `ProviderCapability`
- `JobRun`
- `StageRun`
- `ProgressSnapshot`
- `ProviderProvenance`

The previous probe-only `scripts/s2g_s3a_job_control.py` remains historical
S2G-1X scaffold evidence. New runtime code should use the app service module.

## S3A Boundary

S3A remains dry-run/foundation-only. The planned stages are visible but every
stage has writes disabled:

- `update_check`
- `hydration_read`
- `import_reuse`
- `classification`
- `ai_tagging`
- `localization`
- `summary`

Production S3A execution is disabled. Unattended S3B automation is disabled.

## Safety

This phase did not run or enable:

- production DB writes;
- production import;
- production classification;
- production AI tagging;
- production localization;
- provider/Pixiv/gallery-dl/SauceNAO/Google calls;
- SourceConcept/R1/R2;
- Entity bridge;
- confirmed assignments;
- desired-media backfill;
- cleanup/delete/reset/drop/truncate;
- DB schema migration.

## Recommended Next Phase

Run a bounded S2G provider/load-control validation against a controlled AI
tagging job, still under explicit operator approval and with production S3A
execution kept separate. Production S3A promotion should remain a later phase.
