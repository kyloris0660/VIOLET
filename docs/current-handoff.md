# Current Handoff - V.I.O.L.E.T.

> Last updated during Phase 4.5-SC1 implementation (2026-06-06), after PR #92, PR #93, PR #94, and PR #95 were merged into `main`.
> Read this file at the start of any new conversation before opening older phase reports.

## Repository State

| Item | Value |
|------|-------|
| Repo | `kyloris0660/VIOLET` |
| Canonical URL | `https://github.com/kyloris0660/VIOLET` |
| Historical repo name | `AnimeLocalBooru`; old links may redirect, but active references should use VIOLET |
| Local path | `C:\Users\kyloris\Documents\AnimeLocalBooru` |
| Main branch status | `main` includes PR #92 (`325c51c`), PR #93 (`b1103b2`), PR #94 (`fddedef`), and PR #95 (`5b0a6fa`) as of 2026-06-05 |
| Stack | FastAPI + PostgreSQL 17 + Jinja2/Tailwind + vanilla JavaScript |
| Python | Project venv at `.\venv\Scripts\python.exe` |
| Dev DB | `blombooru` on `localhost:5432` |
| Test DB | `blombooru_test` |
| Dev server | `& "$PY" run.py --debug`; no `PYTHONPATH=<repo>\backend` workaround |

## Accepted Current State

- Phase 3.8d medium pilot is accepted at a practical level. The accepted source label is `violet:phase3.8d:i7:staged-success` with `994` staged-success/imported rows; rows `799`, `839`, `922`, `970`, `971`, and `972` remain deferred unless a separate retry/backfill decision is approved. Traceability: PR [#64](https://github.com/kyloris0660/AnimeLocalBooru/pull/64).
- Phase 4.1 entity metadata foundation is merged and is the baseline for later entity work. Traceability: PR [#68](https://github.com/kyloris0660/VIOLET/pull/68).
- Phase 4.2 manual entity correction/review foundation is merged. Manual review is correction-oriented, not an exhaustive queue. Traceability: PR [#69](https://github.com/kyloris0660/VIOLET/pull/69).
- Phase 4.3-A proper-noun signal trust policy is merged: AI-generated character/copyright/artist/proper-noun tags are weak evidence or query seeds, not reliable entity truth. Traceability: PR [#70](https://github.com/kyloris0660/VIOLET/pull/70).
- Phase 4.3-B source-first strategy is merged: future reliable entity metadata should prioritize source-backed evidence and explicit provider policy, not AI-only proper-noun signals. Traceability: PR [#71](https://github.com/kyloris0660/VIOLET/pull/71).
- Phase 4.4-A no-source source-discovery design is merged: treat the current iCloud-derived library as no-source by default; do not make exact-source inventory the next route. Traceability: PR [#72](https://github.com/kyloris0660/VIOLET/pull/72).
- Server lifecycle guard is merged: run `scripts/audit_active_violet_servers.py` before agent-started or manual-validation servers; do not kill unknown processes. Traceability: PR [#74](https://github.com/kyloris0660/VIOLET/pull/74).
- Phase 4.4-B0/B1/B1V established a tiny SauceNAO route. High-confidence matches for `2687` and `2670` were manually accepted; low-confidence matches for `2690`, `2654`, and `2647` were discarded by default for this workflow. SauceNAO high-confidence results can provide source-backed evidence candidates, but cannot create automatic confirmed assignments or trusted Entity rows. Traceability: PR [#75](https://github.com/kyloris0660/VIOLET/pull/75), [#76](https://github.com/kyloris0660/VIOLET/pull/76), [#77](https://github.com/kyloris0660/VIOLET/pull/77), [#78](https://github.com/kyloris0660/VIOLET/pull/78).
- Phase 4.4-C0 provider-neutral evidence contract is merged. It defines provider-neutral reverse-search DTOs and SauceNAO mapping, preserves raw provider artist/work/character metadata, maps validated high-confidence samples to strong exact/near-exact evidence, maps invalid low-confidence samples to discarded evidence, and performs no provider calls, uploads, DB writes, migrations, confirmed assignments, localization, Entity Resolver, similarity/clustering, source/iCloud mutation, or app-managed storage mutation. Traceability: PR [#79](https://github.com/kyloris0660/VIOLET/pull/79).
- Phase 4.4-C1 validated evidence persistence is merged. It writes only the two manually validated high-confidence SauceNAO results (`2687`, `2670`) into `ProviderCache`, `EntityEvidence`, and suggestion-only `MediaEntityCandidate` rows with `entity_id=NULL`; it creates no `Entity`, no confirmed `MediaEntityAssignment`, no `media_tags`, no `TagTranslation`, no localization execution, and no positive writes for low-confidence `2690`, `2654`, or `2647`. Public report: `docs/reports/phase-4.4c1-validated-evidence-persistence.md`. Traceability: PR [#81](https://github.com/kyloris0660/VIOLET/pull/81).
- Phase 4.4-C1-HF1 hotfix is merged: `EvidencePersistencePlan.db_write_allowed` is enforced in the durable persistence service and the C1 runner explicitly promotes only approved validated plans to writable after all C1 gates pass. Expected DB impact was zero new rows; existing accepted C1 rows remain accepted unless later validation proves a data correction is needed. Public report: `docs/reports/phase-4.4c1-db-write-gate-hotfix.md`. Traceability: PR [#82](https://github.com/kyloris0660/VIOLET/pull/82).
- Phase 4.4-D0/D1 second-provider scouting completed as scouting-only with no live pilot. Corrected decision logic: trace.moe is a specialized anime screenshot/scene provider and is not selected for the current illustration/source-backed metadata route. Best low-cost official pilot candidate is Google Cloud Vision Web Detection, which has official REST/base64 `WEB_DETECTION` support and first-1000-units/month free pricing, but requires Google Cloud credentials/setup and explicit derived-upload approval. Best dedicated reverse-image API candidate is TinEye API, but it requires a paid search bundle and `x-api-key`. Danbooru/Gelbooru remain better metadata lookup candidates after a known post/source ID exists, not no-source reverse-image providers. Public report: `docs/reports/phase-4.4d0d1-second-provider-scouting-and-tiny-pilot.md`.
- Phase 4.4-D1G ran the approved five-sample Google Vision Web Detection tiny pilot using only derived/resized/metadata-stripped images. Current-shell `gcloud` PATH was stale, but the runner found Cloud SDK by absolute path, verified project/quota project `image-project-497811`, Vision API enabled, and ADC token availability without printing token or credential contents. Google Vision returned `exact_source_candidate` for 4 of 5 approved samples and `visually_similar_only` for 1 of 5; results remain local/report-only and are not persisted.
- Phase 4.4-D1G also ran a read-only Pixiv filename source-prior audit over development DB/app-managed metadata only. It found Pixiv-like filename tokens in `555` of `1989` media records (`27.9%`) and `551` distinct candidate work IDs. The approved five Google samples had `0` Pixiv-prior hits and are explicitly not representative for Pixiv-prior coverage. The current DB preserves filename/app-managed basenames enough to detect many priors, but there is no dedicated `original_basename` / source-prior ledger column, so absence of a token remains a metadata retention limitation. Public report: `docs/reports/phase-4.4d1g-google-vision-pixiv-source-prior.md`.
- Phase 4.4-P0 designed the Pixiv filename source-prior automated correspondence gate and re-ran read-only extraction over development DB/app-managed metadata only. It confirmed `555` of `1989` media records (`27.9%`) with Pixiv-like tokens and `551` distinct candidate work IDs. The P0 runner selected a private 30-item feasibility sample from real extracted candidates, but live Pixiv reference lookup was `reference_lookup_policy_blocked` because no official, documented, unauthenticated metadata/preview route was accepted. The runner made `0` Pixiv/provider requests, wrote no DB rows, and kept exact mappings only in ignored `.local_manifests` artifacts. Public report: `docs/reports/phase-4.4p0-pixiv-filename-source-prior-auto-verification.md`.
- Phase 4.4-P2R route scouting supersedes the public-page / preview route direction from PR #86. Bounded prior-art review of gallery-dl, pixivpy, PixivUtil2, and official Pixiv policy surfaces found no official public artwork metadata API and confirmed that mature reliable metadata routes are authenticated or tool-mediated. Recommended next pilot is a manual gallery-dl JSON metadata import pilot with no Pixiv network inside V.I.O.L.E.T.; if that succeeds, design an external gallery-dl adapter. Do not keep polishing PR #86 public HTML/preview probing as the main Pixiv metadata route. Public report: `docs/reports/phase-4.4p2r-pixiv-authenticated-metadata-route-design.md`.
- Phase 4.4-P2R-F5 is merged. PR #92 added provider-neutral source metadata/tag/name/name-registry/alias-candidate/evidence/searchable-name-assertion tables and service support. `SourceSearchableNameAssertion` is a source-search layer only: not `Entity`, not `EntityAlias`, not `MediaEntityCandidate`, not `LocalSourceHint`, not confirmed assignment, and not `media_tags`. Public report: `docs/reports/phase-4.4p2r-f5-provider-neutral-source-name-registry.md`.
- Phase 4.4-P2R-F6 preflight hotfix is merged. PR #93 fixed the local debug startup self-lock in `migrate_add_external_tag_category_lookup_cache()` by using an inspector bound to the active migration connection. This was a narrow startup fix; it did not implement F6 UI/search.
- Phase 4.4-P2R-F6 is merged. PR #94 made F5 source-layer data usable in the media workflow: source assertions/source tags appear separately from ordinary tags, source chips default to ordinary `q=` search behavior, scoped source filters remain available for advanced/debug use, and admin Content layout was cleaned up without starting Entity promotion.
- Phase 4.4-P2R-F7a is merged. PR #95 produced the primary-provider-backed source-name candidate extraction path and final validation pack. F7a candidates are source-layer evidence only; they are not `Entity`, `EntityAlias`, `MediaEntityAssignment`, confirmed assignment, or `media_tags` truth. Public report: `docs/reports/phase-4.4p2r-f7a-llm-source-name-candidates.md`.
- Phase 4.5-SC1 / PR #96 is the active source-concept core route. It aggregates F7a candidates, ordinary/AI tags, source assertions/observations/tags, alias candidates, provider structured fields/cache context, and future provider/manual signals into unconfirmed `SourceConcept` rows. The resolver may use bounded text-only pair adjudication when explicitly enabled and budget/cache recorded, but it must remain source-layer only and must not implement full SC2 search/UI integration or Entity promotion.
- GOV-2 workflow policy is active in this branch: durable core reliability stays strict, while workflow weight decreases for one-off and phase-scoped artifacts.
- GOV-2a reminder: reducing workflow weight does not remove the Chinese final report requirement or the required `工程判断 / 操作员备注` section for non-trivial final reports.

## Active Governance

Reliability remains strict for:

- DB schema/migrations.
- Provider-neutral evidence contracts.
- Entity / Alias / Evidence / Candidate / Assignment lifecycle.
- `ProviderCache`, `EntityEvidence`, `MediaEntityCandidate`, and `NegativeLookupCache` write semantics.
- Provider privacy/upload gates, budget/rate-limit/cache/audit design, and separate run approval.
- Confirmed assignment policy: manual confirmation or explicitly approved policy only.
- Source/iCloud/app-managed storage mutation safety.
- Broad or repeated provider runs, which require ledger discipline.
- E2E delivery when E2E is in scope: 0 failures required; skipped tests must be explicitly gated.

Workflow weight must decrease:

- Prefer executable guards, assertions, DB constraints, transaction boundaries, enum states, allowlists/denylists, and focused tests.
- Avoid repeated docs-only gates, infinite reviewer/process loops, overly fragmented phases, and generic frameworks for one-off scripts.
- Classify every new script/tool/report/artifact as durable production code, reusable validation/safety tool, phase-scoped operational runner, one-off local artifact/ignored output, or public report/handoff/roadmap update.
- Default reviewer closeout is 1-2 bounded fix rounds. P1/P2 is a signal, not an automatic block; lifecycle plus current-stage impact decides.
- Must-fix reviewer categories include current-stage data corruption, DB writes executed by the PR, privacy leaks, provider upload safety, current-stage report truthfulness, confirmed assignment/media_tags/entity truth pollution, core contract/schema correctness actually consumed by the PR, and irreversible operation safety.
- Findings that only matter in a later DB-writing or broad-scaling phase should move into that phase's acceptance criteria instead of blocking a non-mutating design PR indefinitely.
- Do not split phases unless the split reduces real risk or improves delivery clarity. Small docs-only updates should usually be batched unless they remove major contradictions or unblock current work.

## Current Recommended Route

Near-term route during Phase 4.5-SC:

1. Treat PR #95/F7a as one input adapter, not the scope boundary. The SourceConcept resolver must aggregate all current source-layer name, tag, assertion, observation, alias, provider structured field, AI/model, and future manual/provider signals.
2. Keep SC1 focused on additive schema, signal adapters, resolver core, run ledger, evidence/link/search-preview tables, documentation, and final validation pack.
3. Keep SC2 separate: full search expansion, UI concept grouping/chips, evidence preview, and manual promotion preview belong after the core resolver is reviewed.
4. Keep all SourceConcept rows unconfirmed. Do not create or mutate `Entity`, `EntityAlias`, `EntityEvidence`, `MediaEntityCandidate`, `MediaEntityAssignment`, `LocalSourceHint`, confirmed assignment, `TagTranslation`, or `media_tags`.
5. Do not run gallery-dl, Pixiv/SauceNAO/Google/provider calls, LLM extraction/classification, tag localization batches, background translation, broad scans, imports, or image uploads unless a later phase explicitly approves that run. SC1's explicit, bounded, text-only LLM pair adjudication is the narrow exception for resolver validation and must remain cache/budget recorded with no fallback provider by default.
6. Phase 3.9 remains required before broad/repeated provider runs, `100+` scale, 5k/10k scale, large cache population, full-library scheduling, or full-library import.

Do not treat older "blocked until X" wording in historical reports as current unless this handoff or the roadmap repeats it as active.

## Validation Starting Points

- Python identity: `& "$PY" scripts/check_python_env.py --expected-python "$PY"`.
- Manual development validation: `docs/manual-validation.md`.
- Test selection: `docs/test-workflow.md`.
- Source/iCloud safety: `docs/icloud-safe-ingestion.md`.
- Latest roadmap route and development standards: `docs/project-roadmap.md`.

## Hard Non-Goals Without Explicit Approval

- No push to `main` and no merge by agents.
- No DB migration, DB import, or DB write outside an approved phase.
- No provider call/upload outside approved provider/run policy.
- No original image upload by default.
- No source/iCloud mutation.
- No app-managed storage mutation.
- No classification, AI tagging, localization execution, Entity Resolver execution, similarity/clustering, confirmed assignment, trusted Entity creation, or media_tags mutation unless explicitly in scope.

## Traceability Notes

Historical reports under `docs/reports/` are archival. Do not rewrite them to update current policy; create a new report for new governance changes. Historical links containing `AnimeLocalBooru` are acceptable when they are archival GitHub redirects, but new active links should use `kyloris0660/VIOLET`.
