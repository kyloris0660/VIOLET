# Phase 4.6-FULLLIB-E1a Production Runner Dry-Run Proof

## 1. Summary

E1a added and ran a dry-run-only production FULLLIB runner. The run inspected a bounded local input set, wrote private inventory ledgers, generated a future batch plan, and produced this privacy-safe public report. It did not execute import, classification, AI tagging, provider, LLM, SourceConcept, Entity, DB write, app-storage write, source write, server start, or browser validation.

## 2. Current state

PR #108 / GOV3 and PR #110 / FULLLIB-P0 are merged into `main`. This branch starts the implementation track for production utility work while keeping the SourceConcept/provider/entity track paused.

## 3. Current-head reviewer fix

This current-head fix streams source traversal and stops at `--max-files`, keeps repo-compatible Python preflight layouts, prevents DB identity overclaiming against app settings resolution, ledgers metadata access failures as deferred rows, records truthful worktree report provenance, and uses SQLAlchemy field-based URL construction for DB passwords with reserved characters.

## 4. Parallel feature development

Parallel feature development is intentionally paused during FULLLIB. R1R, A1R, R2, SourceConcept, provider, and Entity work remain out of scope unless explicitly resumed later.

## 5. Runner design

The runner is a phase-scoped operational runner. In E1a it supports `--dry-run`, `--inventory-only`, `--output-dir`, `--write-public-report`, `--source-root`, `--production-db-url`, `--production-storage-root`, `--max-files`, and `--batch-size`. Execute mode is present only as a future guard and requires the exact confirmation phrase before it is rejected as not implemented in E1a.

## 6. Source root safety

Input locations are protected as read-only. The dry-run rejects missing inputs, repo overlap, production storage overlap, test storage overlap, output overlap, network/NAS paths, and unsafe output placement. Public artifacts redact all local paths and source names.

## 7. Production DB/storage identity design

E1a validates the intended production DB configuration without connecting or writing. The accepted production database name is `violet_library_prod`. App-equivalence status is `proven_match` and `urls_match=True`; E1a does not claim app equivalence unless settings/env/CLI resolution actually match. Production storage is validated as an explicit, non-overlapping app-managed storage root; E1a does not create directories or write files there.

## 8. Inventory dry-run results

- Files seen: 5
- Supported candidates: 4
- Eligible before duplicate check: 3
- Unique future import candidates: 2
- Max files reached: True

## 9. Duplicate/deferred/unsupported summary

- Duplicate items: 1
- Unsupported items: 1
- Deferred items: 1
- Hash failures: 0

## 10. Batch plan

- Planned batches: 1
- Batch size: 2
- Planned unique candidates: 2
- Dry-run only: true

## 11. Future import execution plan

E1b must run a fresh inventory dry-run against approved production inputs, verify production DB/storage identity, prove backups and recovery, then import only gate-allowed unique candidates in bounded batches after explicit execute approval.

## 12. Future classification plan

Classification remains a post-import E1b stage for newly imported media only. It must record job accounting and content-class distribution before/after without source or storage mutation beyond the approved import outputs.

## 13. Future AI tagging plan

AI tagging remains a post-classification E1b stage for eligible imported media only. It must use the local WD tagger, preserve manual/locked tags, disable localization side effects, and record model provenance and coverage.

## 14. AI tag fingerprint reuse/export plan

Future reuse should key by `content_sha256`, compatible MD5, file size, media dimensions, model identity, tagger code identity, and thresholds. Compatible exports may replay tags through existing tag-service semantics; policy mismatches must defer or infer locally.

## 15. Localization handling

No LLM translation ran. Existing display behavior is DB/static localization first and canonical tag fallback otherwise. Newly generated AI tags will display Chinese names immediately only when existing static or DB translations cover them. E1b should emit a post-AI-tagging localization gap report; an optional later `FULLLIB-L1` can backfill translations under a separate approval.

## 16. Browser validation requirement for E1b

E1b is not complete until controlled browser/gallery validation passes, even if UI code is unchanged. It must verify imported media in gallery, thumbnails load, detail page opens, AI tags display, tag search returns imported media, localized tag display works where translations exist, no broken images appear, no private local source paths are exposed, and server identity is correct.

## 17. Contract mapping

E1b mapping: `python_env_contract_v1`, `postgres_db_contract_v1`, `media_import_contract_v1`, `classification_contract_v1`, `ai_tagging_contract_v1`, `mutation_safety_contract_v1`, `artifact_lifecycle_contract_v1`, `public_redaction_contract_v1`. E1a does not claim import, classification, or AI tagging target completion.

## 18. Safety proof

The dry-run wrote only private ledgers under the chosen output directory and public report artifacts under `docs/reports`. It did not write DB, source, iCloud, app-managed storage, provider caches, SourceConcept tables, Entity truth, localization tables, or `media_tags`.

## 19. Validation commands and results

- `python.exe scripts/run_phase46_fulllib_e1_production_import_ai_tagging.py --dry-run [paths redacted]`: passed
- `no DB connection attempted by E1a runner`: passed
- `no source/app-storage write attempted by E1a runner`: passed

## 20. Remaining blockers before E1b execute

E1b still needs:

- `approved_real_production_source_input_set`
- `approved_production_storage_root`
- `non_mutating_production_db_connection_proof`
- `backup_recovery_proof`
- `offline_model_preflight`
- `fresh_e1b_dry_run_ledgers`
- `contract_checks`
- `explicit_execute_approval`

## 21. Recommended E1b prompt outline

Start from latest `main`, stay on the production utility track, run no provider/LLM/SourceConcept/Entity stages, verify production DB/storage identity, run fresh inventory dry-run, prove backups and contracts, then stop before execute unless the prompt includes `EXECUTE_PHASE46_FULLLIB_E1_PRODUCTION_IMPORT_AI_TAGGING`. If execute is approved, import in bounded batches, classify, AI tag, run real browser/gallery validation, generate public/private artifacts, push PR, comment `@codex review`, and stop.
