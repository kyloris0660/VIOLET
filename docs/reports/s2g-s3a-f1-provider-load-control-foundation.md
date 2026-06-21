# S2G/S3A-F1+G1: WDTagger Provider, Load Control, and DirectML Attempt

Status: target_met.

Contract: `s2g_s3a_f1_foundation_contract_v1`.

Public summary: `docs/reports/s2g-s3a-f1-provider-load-control-foundation-summary.json`.

## Summary

This PR now covers the F1 foundation plus a bounded G1 GPU/DirectML enablement
attempt. It still does not enable production S3A execution.

Implemented now:

- WDTagger provider abstraction for `CUDAExecutionProvider`,
  `DmlExecutionProvider`, and `CPUExecutionProvider`.
- Truthful provider fallback reporting.
- Bounded CPU load-control defaults and caps after settings/env parsing.
- Configured vs effective batch-size provenance.
- Provider/model/backend/load provenance in current AI tagging runtime outputs.
- Shared job/progress/load/provider vocabulary in
  `backend/app/services/job_control.py`.
- Dry-run-only S3A stage planning vocabulary.
- Project-venv-only DirectML package installation attempt and tiny bounded
  CPU vs DirectML benchmark evidence.

## Provider Behavior

Requested provider preference:

```text
CUDAExecutionProvider,DmlExecutionProvider,CPUExecutionProvider
```

ONNX Runtime providers available after the DirectML attempt:

```text
DmlExecutionProvider,CPUExecutionProvider
```

Actual loaded provider for the default preference:

```text
DmlExecutionProvider
```

Fallback occurred only for the skipped provider before the selected provider:

```text
unavailable_requested_providers=CUDAExecutionProvider
```

CPU-only preference was also validated and did not report fallback:

```text
CPUExecutionProvider
```

## DirectML / CUDA Attempt

DirectML:

- `onnxruntime-directml` was installed only into the project virtual
  environment.
- Installed version: `1.24.4`.
- ONNX Runtime imported version after the attempt: `1.24.4`.
- `DmlExecutionProvider` was exposed by ONNX Runtime and loaded by WDTagger.
- The smoke run reported an ONNX Runtime warning that some graph nodes were
  assigned outside the preferred execution provider; DirectML still loaded and
  completed inference.

CUDA:

- `onnxruntime-gpu` was not installed.
- `CUDAExecutionProvider` was not exposed.
- CUDA blocker: `package_missing`.

No global or system Python was modified.

## Bounded Benchmark

The smoke benchmark used synthetic zero arrays, two samples, local model cache
only, no production DB writes, and no media tag writes.

| Provider | Status | Samples | Throughput items/sec |
| --- | --- | ---: | ---: |
| `CPUExecutionProvider` | completed | 2 | 2.2820 |
| `DmlExecutionProvider` | completed | 2 | 5.0729 |
| `CUDAExecutionProvider` | provider_unavailable | 0 | N/A |

The DirectML result proves provider load and inference capability, not a
production throughput guarantee. CPU fallback remains valid.

## Load Control

Effective defaults and caps now available through settings/env:

| Setting | Effective value in smoke |
| --- | --- |
| `AI_TAGGING_PROVIDER_PREFERENCE` | `CUDAExecutionProvider,DmlExecutionProvider,CPUExecutionProvider` |
| `AI_TAGGING_CPU_INTRA_OP_THREADS` | `4` |
| `AI_TAGGING_CPU_INTER_OP_THREADS` | `1` |
| `AI_TAGGING_PREPROCESS_WORKERS` | `2` |
| `AI_TAGGING_EXECUTION_MODE` | `ORT_SEQUENTIAL` |
| `AI_TAGGING_PROCESS_PRIORITY` | `below_normal` |
| `AI_TAGGING_BATCH_SIZE` configured | `2` |
| effective runtime batch size | `2` |
| batch cap source | `configured` |
| max concurrent AI jobs | `1` |

Operator/env overrides are clamped before ONNX Runtime session options or job
load-control summaries use them. `AI_TAGGING_PROCESS_PRIORITY` is recorded but
not applied to the shared FastAPI process in this phase.

## Provenance

AI tagging runtime summaries now expose:

- model name;
- model repo id;
- thresholds;
- requested provider preference;
- actual ONNX provider loaded;
- fallback reason when fallback occurs;
- configured batch size;
- effective runtime batch size;
- batch cap source;
- model optimal batch size;
- CPU intra/inter thread settings;
- preprocess workers;
- execution mode;
- tagger version/source;
- backend.

Admin job serialization reports `current_runtime_provenance` with
`provenance_scope: current_runtime_not_historical`. No DB schema migration was
added, and completed historical jobs do not yet have durable per-job provider
provenance. Durable per-job provenance persistence should be a later
schema-backed phase.

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
- DB schema migration;
- model download during the smoke run.

## Recommended Next Phase

Keep DirectML as an optional local runtime path, then run a separately approved
bounded real AI tagging validation job before any production S3A promotion.
Production S3A execution should remain a later operator-approved phase.
