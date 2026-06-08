# 4.5-SCV2-P0 Controlled Medium Expansion and Source Metadata Run Policy

## Summary

P0 performed a read-only inventory over the current development DB and produced a governed split for medium expansion. It did not import, stage-copy, scan source roots, run AI/classification/localization jobs, run providers, run gallery-dl/Pixiv, run LLMs, start a server, or mutate DB/source/app storage.

- Recommended next executable phase: `SCV2-E1`.
- Current media: `1989`; eligible media: `1936`.
- Eligible AI tag coverage: `1936` / `1936` (`100.0%`).
- Pixiv-like media candidates already in DB: `557`.
- Pixiv-like candidates with source metadata: `60`.
- Pixiv-like metadata backlog: `497`.
- User claim confirmed: `True`.

## Current Baseline After SCV1

- Total media: `1989`.
- Eligible policy: `content_class IN ('anime', 'unknown')`.
- Eligible media: `1936` (`97.34%`).
- Non-anime count: `53`.
- Content class distribution: `{"anime": 1882, "non_anime": 53, "unknown": 54}`.
- Media with any tags: `1962`.
- Eligible media without AI tag provenance: `0`.
- Media with source-layer signals: `1338`.
- Media with SourceConcept evidence or links: `1338`.
- Source metadata distinct media coverage: `60`.
- Media without source metadata: `1929`.

## Current Pixiv-like Media Already In DB

- Method: `DB-derived signals only; no source root scan, provider call, gallery-dl, or filesystem inventory.`.
- Distinct Pixiv-like candidates: `557`.
- With existing source metadata: `60`.
- Without source metadata: `497`.
- With Pixiv source metadata: `60`.
- With AI tag provenance: `556`.
- Without AI tag provenance: `1`.
- Eligible / non-eligible Pixiv-like media: `556` / `1`.
- Reason category counts: `{"filename_pixiv_id_pattern": 557, "filename_pixiv_marker": 2, "source_assertion_provider_pixiv": 56, "source_concept_evidence_provider_pixiv": 60, "source_concept_signal_provider_pixiv": 60, "source_metadata_pixiv_work_id": 60, "source_metadata_provider_pixiv": 60, "source_name_candidate_provider_pixiv": 57, "source_name_observation_provider_pixiv": 60, "source_tag_observation_provider_pixiv": 60}`.
- Duplicate Pixiv ID groups detected: `3`.
- Marker-only / invalid ID candidates: `0`.
- Ambiguous Pixiv ID candidates: `0`.

Assessment: already-imported Pixiv-like media likely far exceed metadata-covered media, so the user's intuition is confirmed by DB-derived signals if `user_claim_confirmed` is true above.

## Current Source Metadata Gap

- Source metadata rows: `200`; linked rows: `60`.
- Distinct media covered: `60` (`3.02%`).
- Distinct eligible media covered: `60` (`3.1%`).
- Distinct Pixiv-like media covered: `60`.
- Distinct Pixiv-like media missing metadata: `497`.
- Already imported non-Pixiv media lacking metadata: `1432`.
- External/new expansion candidates: `{"known_in_p0": false, "reason": "P0 is forbidden from scanning source or cloud roots or staging candidates."}`.
- Source metadata by provider: `{"danbooru": 22, "gelbooru": 21, "google_vision": 1, "no_tag_provider": 22, "pixiv": 97, "saucenao": 37}`.
- Source tag observations by provider: `{"danbooru": 88, "gelbooru": 63, "google_vision": 2, "pixiv": 556, "saucenao": 1}`.
- Source name observations by provider: `{"danbooru": 66, "gelbooru": 63, "pixiv": 236, "saucenao": 94}`.
- Source assertions by provider/status: `{"pixiv": 283, "saucenao": 17}` / `{"needs_review": 5, "rejected": 113, "searchable_active": 182}`.
- Source name candidates by provider/status: `{"local_media_tags": 51, "pixiv": 852}` / `{"active": 903}`.
- SourceConcept evidence by provider/status/type: `{"ai_wd": 4439, "danbooru": 286, "gelbooru": 252, "google_vision": 2, "local_media_tags": 127, "pixiv": 3604, "pixiv_parenthetical_pattern": 178, "pixiv_provider_canonical": 4, "saucenao": 425}` / `{"active": 1103, "needs_review": 2581, "superseded": 5633}` / `{"ai_model_tag": 4439, "f7a_candidate": 1735, "provider_structured_field": 612, "source_alias_candidate": 182, "source_assertion": 442, "source_name_observation": 1044, "source_tag_observation": 863}`.

## AI Tag Continuity Policy

- Current complete: `True`.
- Future invariant: Every newly imported eligible media item must receive AI tag provenance after import/classification.
- E1 acceptance criterion: `{"allowed_deviation": "Only approved item-level failures with reason may reduce the ratio.", "metric": "eligible_new_media_with_ai_tag_provenance / eligible_new_media", "requires_item_level_failures_recorded": true, "target_pct": 100.0}`.
- AI tags remain provenance/signal, not Entity truth.
- AI expansion must not auto-trigger localization unless explicitly approved.

## Controlled Medium Expansion Target

- Target total range: `{"max": 4000, "min": 3500, "recommended": 3750}`.
- Recommended successful imported media count: `1761`.
- Over-selection buffer ratio/count: `1.35` / `2378`.
- Failure budget: `{"max_consecutive_failures": 10, "max_failure_rate": 0.05, "max_item_failures": 20, "max_same_reason_failures": 20, "note": "Use existing medium-pilot defaults unless user/ChatGPT approves a different E1 budget."}`.

## Candidate Selection Policy

- already hydrated/readable local files.
- likely anime/illustration.
- likely Pixiv-origin filename/source prior.
- not already imported.
- safe extension.
- no path escape.
- no source or cloud write.
- not cloud placeholder unless explicitly allowed by later phase.
- enough over-selection to tolerate duplicates and item-level failures.

## Import/AI/Pixiv/SourceConcept Phase Split

### SCV2-E1 - Medium Import + AI Tag Completion

Purpose: Add enough media to bring DB to roughly 3.5k-4k and complete AI tag provenance for newly imported eligible media.

May do: `["approved source-root candidate selection", "staging audit", "DB import if separately approved", "classification/eligibility", "AI tagging for newly imported eligible media", "item-level ledger"]`.
Must not do: `["Pixiv/gallery-dl/provider metadata", "SourceConcept resolver improvements", "Entity bridge", "tag localization unless separately approved", "media_tags truth beyond approved AI provenance behavior"]`.

### PX1 - Bounded Pixiv/Source Metadata Extraction

Purpose: Run bounded metadata-only Pixiv/gallery-dl extraction for DB Pixiv-like candidates after provider policy approval.

May do: `["metadata-only gallery-dl/Pixiv requests", "cache/checkpoint/retry/rate-limit", "source-layer metadata/observations/assertions writes if approved", "public/private artifact split"]`.
Must not do: `["import media", "download original images by default", "upload images", "AI jobs", "classification jobs", "Entity truth", "media_tags mutation", "TagTranslation mutation"]`.

### SCV2-R1 - SourceConcept Alias Resolver / Needs-Review Triage

Purpose: Use expanded AI/source metadata signals to improve alias closure and reduce needs_review noise.

May do: `["approved SourceConcept source-layer writes", "alias resolver/closure improvements", "needs_review triage metrics", "CJK/English/Danbooru alias gap handling"]`.
Must not do: `["Entity truth", "confirmed assignments", "media_tags mutation", "SourceConcept editing UI unless separately approved"]`.

### SCV2-A1 - Post-expansion Audit

Purpose: Compare pre/post expansion and decide route.

May do: `["read-only pre/post count comparison", "AI coverage audit", "Pixiv/source metadata coverage audit", "SourceConcept status/gap/search symmetry audit", "redaction proof"]`.
Must not do: `["new import", "new provider calls", "new AI/localization jobs", "Entity bridge", "truth writes"]`.

## Ledger Schema

- Medium import ledger required fields: `["run_id", "item_id", "source_label", "source_root_id", "originalFileNameRedactedOrHashed", "detected_pixiv_id", "cloud_hydration_state", "file_extension", "size", "import_candidate_status", "staging_status", "import_status", "media_id", "duplicate_of_media_id", "content_class", "eligible_for_ai_tagging", "ai_tag_status", "ai_tag_job_id", "ai_tag_failure_reason", "eligible_for_pixiv_metadata", "deferred_reason", "public_safe_label", "private_artifact_ref"]`.
- Pixiv metadata ledger required fields: `["run_id", "media_id", "detected_pixiv_id", "page_index", "metadata_request_status", "provider", "method", "authenticated", "original_downloaded", "cache_hit", "retry_count", "failure_reason", "source_metadata_record_id", "tag_observation_count", "name_observation_count", "source_assertion_count", "redaction_status", "eligible_for_source_concept_resolver", "private_artifact_ref", "public_safe_label"]`.
- P0 does not implement DB ledger schemas; JSONL/CSV artifacts are proposed for E1/PX1 unless a later phase promotes them.

## Safety Gates And Stop Conditions

- Structural stop conditions: `["DB identity mismatch", "transaction is not read-only during P0", "public redaction scan fails", "forbidden table counts change", "answering inventory requires source scan/provider call/DB write/import", "public report would expose local paths, filenames, secrets, or exact private source locators"]`.
- E1 gates: `["import target and over-selection buffer approved", "source roots identified safely", "source and cloud mutation guard available", "app storage target verified", "item-level ledger defined", "failure budget defined", "AI tagging queue behavior controlled", "localization auto-run disabled or explicitly approved", "public/private artifacts safe"]`.
- PX1 gates: `["candidate Pixiv-like media count known", "gallery-dl / provider policy approved", "cookie/auth policy approved if needed", "no-original-download policy defined", "rate limit, timeout, retry, cache/checkpoint defined", "source metadata write boundaries defined", "redaction and public/private reporting defined"]`.
- R1 gates: `["SourceConcept write scope approved", "no-truth-write proof available", "source-layer-only table boundaries defined", "alias/needs_review metrics from SCV1/PX1 available"]`.

## Public/Private Artifact Boundary

- Private artifact root label: `.local_manifests/phase-4.5-scv2-p0-controlled-medium-expansion-policy`.
- Private artifact names: `["db-identity.json", "current-media-baseline.json", "current-pixiv-like-media-inventory.json", "current-source-metadata-gap-inventory.json", "ai-tag-coverage-baseline.json", "medium-expansion-candidate-policy.json", "medium-expansion-ledger-schema.json", "pixiv-source-metadata-ledger-schema.json", "phase-split-plan.json", "risk-and-stop-conditions.json", "public-redaction-check.txt"]`.
- Exact media IDs, local paths, filenames, source locators, and exact Pixiv IDs are not public: `True`.

## Decision Matrix

- Answers: `{"already_imported_pixiv_like_count": 557, "eligible_ai_coverage_complete": true, "medium_expansion_target_successful_imports": 1761, "pixiv_like_metadata_backlog": 497, "recommended_next_executable_phase": "SCV2-E1", "source_metadata_eligible_coverage_pct": 3.1, "user_claim_confirmed": true}`.
- Options: `[{"key": "SCV2-E1_medium_import_ai_completion", "priority": "P1", "reasons": ["User wants roughly doubled scale before serious resolver/triage work.", "AI coverage is currently complete for eligible media, so newly imported eligible media must preserve that distribution.", "Import and AI completion can run without provider/Pixiv calls if kept in E1."], "recommended": true}, {"key": "PX1_pixiv_source_metadata_extraction", "priority": "P1 after E1", "reasons": ["Pixiv-like DB backlog is 497 of 557 Pixiv-like candidates.", "Provider/gallery-dl needs a separate policy, auth, cache, retry, and redaction gate."], "recommended": true}, {"key": "SCV2-R1_source_concept_alias_triage", "priority": "P2 after PX1", "reasons": ["Resolver/needs_review work benefits from expanded media and source metadata signals.", "Still must remain source-layer-only with no truth writes."], "recommended": true}, {"key": "five_k_ten_k_or_full_library", "priority": "deferred", "reasons": ["Not needed to expose medium-scale behavior.", "Requires stronger production ingestion/source item ledger and broad-run discipline."], "recommended": false}]`.

## Recommended Next Executable Phase

`SCV2-E1` should be next only after user/ChatGPT approve the import target, source roots, staging/import safety, AI job behavior, and item ledger. E1 must not include Pixiv/gallery-dl/provider execution.

## Deferred Work

- PX1 provider/gallery-dl/Pixiv metadata execution.
- R1 SourceConcept alias resolver / needs_review triage.
- A1 post-expansion audit.
- 5k/10k/full-library import, Entity bridge, SourceConcept editing UI, confirmed assignments, and media_tags truth writes.

## Validation

- Operational inventory command: `python scripts/run_phase45_scv2_p0_controlled_medium_expansion_policy.py --output-dir .local_manifests/phase-4.5-scv2-p0-controlled-medium-expansion-policy --write-public-report --read-only`.
- Operational inventory result: `passed`.
- PostgreSQL transaction_read_only: `on`.
- Forbidden table count changes: `[]`.
- Public redaction passed: `True`.
- Real browser validation: N/A; P0 is non-UI/non-runtime and server/browser validation is explicitly forbidden.

## Safety Confirmation

- No push main.
- No merge.
- No DB write, migration, import, cleanup/delete/reset/drop/truncate.
- No source, cloud, staging, or app-managed storage mutation.
- No AI tagging, classification, localization, LLM, provider call, gallery-dl, Pixiv network call, or server/browser validation.
- No Entity Resolver, similarity, SourceConcept implementation, SourceConcept editing, Entity bridge, confirmed assignment, or media_tags mutation.

## Artifact Lifecycle

`{".local_manifests/phase-4.5-scv2-p0-controlled-medium-expansion-policy": "one-off local artifact / ignored output", "docs/reports/phase-4.5-scv2-p0-controlled-medium-expansion-policy-summary.json": "public report / handoff / roadmap update", "docs/reports/phase-4.5-scv2-p0-controlled-medium-expansion-policy.md": "public report / handoff / roadmap update", "scripts/run_phase45_scv2_p0_controlled_medium_expansion_policy.py": "phase-scoped operational runner", "tests/test_phase45_scv2_p0_controlled_medium_expansion_policy.py": "phase-scoped validation test"}`.

## Engineering Judgment / Operator Notes

P0 answers enough to start E1 planning approval because it establishes the current DB baseline, confirms AI tag continuity, verifies a DB-derived Pixiv-like metadata backlog, and defines ledgers, budgets, gates, and phase boundaries. The phase is appropriately narrow: it is more than a prose plan because it creates a reproducible read-only inventory runner, but it does not blur into import/provider/AI execution. E1 must not start until the target, source roots, staging/import safety, AI job controls, localization-off behavior, and item ledger are explicitly approved.
