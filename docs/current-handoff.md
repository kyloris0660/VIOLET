# Current Handoff - V.I.O.L.E.T.

> Generated from `docs/state/current-phase.json`; this file is not the fact source.

## Current Facts

- Phase: `SCV2-SV1B` — Controlled Pixiv Metadata, Localization, and Source-Graph Closure.
- Repository / PR: `kyloris0660/VIOLET` / Draft PR #139.
- Branch: `codex/scv2-sv1b-pixiv-metadata-localization-source-graph-closure`.
- Accepted mainline base: `46861489fa0b3b05ae917a99a3932897efd70365`.
- Implementation evidence HEAD: `2e85b9ee54625cb08a02dfe15599446d96819e32`.
- Status: `sv1b_fresh_replay_v2_graph_passed_pending_search_validation`.
- `target_met=false`; `safe_to_merge=false`; `route_approved=false`.
- `manual_acceptance_status=not_started_replay_recovery`; `next_phase_started=false`.

## Completed Checkpoints

- `checkpoint_a_accepted_baseline`: `passed` — `681d16aaefb390177bec54dd113e626a8a6f3408f89ba2be92d0caca195752b4`.
- `checkpoint_b_primary_phase_delta`: `passed` — `2243ef27f0ce29399caa367af8c88547286d4ffe003df445cbe0ce707df5ed19`.
- `provider_acquisition`: `accepted_immutable_input` — `df6008c1b469beaf9bd7f47e8a9af460188b2ad7e1218366a76e2d17e77d8636`.
- `localization_accounting`: `closed_with_one_manual_pending` — `41dcd1db481544dac6805000e678c98af03332cd592c557331c997df2293c3bd`.
- `r2r_exact_remap`: `passed` — `25090761abff2c2ae9f7ef8d9ea04904c47a9f3a43ce03ab660a39502ae792fc`.
- `primary_graph_safety`: `passed` — `93cce55769a9912090446020c655e4c7ac73fe19c2e61946713110028caddf9f`.
- `failed_retry2_replay_forensic_capture`: `preserved_immutable` — `efff5ee1746ba961552da57304ef1726250d063921d9e5551ef0fef5c9e92c0d`.
- `doc_gov_01`: `passed` — `7c16783dab9a146c284ebfd4f9f66124b9b8ac88156bd573b5acaddf1a26ec4b`.
- `stable_replay_package_v2_offline_validation`: `passed_with_primary_immutable_evidence_crosscheck` — `640c52445524aa69f540a64a41800b9eb5a746d9a234ba6582b5a2ef1feb7845`.
- `fresh_replay_v2_create_import_round_trip`: `passed` — `935f82bdadd502471240c04eac03c400f5c808978d891832b71f95efa2069ab9`.
- `fresh_replay_v2_graph_recovery_and_logical_comparison`: `passed_with_failed_checkpoints_preserved` — `60dac8c58184dbecebb1798ddb5dcb7f112d6096d7fac656d9373cf135c6f089`.

## Current Blocker And Owner Decision

- Blocker: `pending_sv1b_fresh_replay_search_validation` (Primary and fresh Replay search lifecycle and media-level AND semantics).
- Resolution: Stable-signal graph recovery and Primary/fresh logical comparison passed with both failed graph proofs preserved. Run the offline search lifecycle and media-level AND comparison; no provider, LLM, media, or old-Replay route is authorized.
- Failed retry2 Replay: `immutable_forensic_checkpoint`; no in-place repair.
- Package strategy: `sv1b.stable-replay-evidence.v2` with stable source keys/fingerprints only.
- Fresh Replay creation limit: `1`; external-call budget: `0`.
- `enpera` remains one governed localization manual case and is not a Replay blocker.

## Allowed / Forbidden

- Allowed: DOC-GOV-01, package-v2 offline validation, immutable-evidence cross-check, one fresh Replay, independent graph/search validation.
- Forbidden: mutation of failed retry2 Replay; provider/Pixiv/gallery-dl/LLM/media calls; Primary/acquisition/localization replay.
- Forbidden: production, FL1, Provider-2, Entity/truth/media_tags promotion, merge, Ready transition, reviewer trigger, main push, or force-push.

## Next Action

- Required checkpoint: `fresh_replay_search_lifecycle_and_media_level_and_validation`.

## Durable Links

- [Current mainline roadmap](roadmap/current-mainline-roadmap.md)
- [Phase contracts](phase-contracts.md)
- [Replay provenance incident](reports/phase-4.5-scv2-sv1b-replay-trusted-provenance-checkpoint.md)
- [Stable replay evidence v2 ADR](decisions/ADR-0001-stable-replay-evidence-v2.md)

## Deferred Debt

- `DOC-GOV-02` — owner: FL1 planning owner; due before: `FL1 planning`; Separate project-roadmap history into an archive, extract the detailed AGENTS runbook, and remove stale text that still names R1R as the current next phase.
Updated: `2026-07-26T01:18:04+08:00`.
