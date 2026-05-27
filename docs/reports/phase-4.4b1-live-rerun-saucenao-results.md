# Phase 4.4-B1 - SauceNAO Live Rerun Results

Date: 2026-05-27T07:00:12+00:00

## Summary

- Status: `completed`
- Stop condition: `N/A`
- Provider selected: `saucenao`
- Provider category: `saucenao_style_reverse_search`
- Credential present: `True`
- Live requests attempted: `5`
- Derived inputs generated: `5`
- DB writes attempted: `False`
- Confirmed assignments created: `0`

## Closeout Hardening

- Credential-required remains current stop condition: `False`
- Rerun command includes no-active-server preflight args: `True`
- Partial live-run accounting preserves attempted items: `True`
- Mid-run provider stop status: `partial_run_stopped`
- DB writes deferred until first live behavior validation: `True`

## Provider Selection

Selected SauceNAO because it is an official reverse image search service with anime/illustration-relevant indexes and is the best fit for no-source anime illustration discovery. Exact booru APIs remain second-step verifiers after source discovery; trace.moe is screenshot-oriented; IQDB automation lacks a verified stable official API for this pilot.

- API reference status: `official_search_api_entrypoint_known_but_automated_fetch_returned_403`
- Terms status: `official_legal_page_reviewed`
- Rate-limit status: `account_based_limits_noted_from_official_terms; live execution requires local credential and provider-docs-verified flag`

## Approved Sample Gate

- Approved media IDs: `2690, 2687, 2670, 2654, 2647`
- Requested media IDs: `2690, 2687, 2670, 2654, 2647`
- Found media count: `5`
- Eligible count: `5`
- Blocked count: `0`

Blocked reasons:

- none

## No-active-server Preflight

- Result: `clean`
- Listener backend: `windows_netstat`
- Occupied ports: `0`
- Confirmed V.I.O.L.E.T. servers: `0`
- Suspected V.I.O.L.E.T. servers: `0`

## DB / Storage Identity Proof

- `VIOLET_ENV`: `development`
- Configured DB host: `localhost`
- Configured DB port: `5432`
- Configured DB user: `postgres`
- Configured DB name: `blombooru`
- Actual DB name: `blombooru`
- DB identity result: `development_blombooru_confirmed`
- DB password included: `false`
- Storage root mode: `code_root_default`
- Local paths redacted: `True`

## Derived Input Policy

- Input kind: `derived_resized_stripped_image`
- Transform policy: `phase44b1-derived-resized-stripped-v1`
- Max dimension: `768`
- Original upload: `false`
- Thumbnail upload: `false`
- Derived upload attempted: `5`
- Public hashes included: `false`

## Request Results

- Requests attempted: `5`
- Requests skipped: `0`
- Partial run stopped: `False`
- Stop reason: `N/A`
- SauceNAO header.status values: `[0, 0, 0, 0, 0]`
- SauceNAO short_remaining values: `[3, 2, 1, 1, 1]`
- SauceNAO long_remaining values: `[99, 98, 97, 96, 95]`
- SauceNAO minimum_similarity values: `[35.63, 52.0, 37.66, 52.0, 51.7]`
- Short quota exhausted: `False`
- Daily quota exhausted: `False`
- Out of searches: `False`
- Provider availability: `available`
- Total wait seconds: `40`

- `high_confidence_match`: `2`
- `low_confidence_match`: `3`

Per-item final states:

- media `2690`: `low_confidence_match`
- media `2687`: `high_confidence_match`
- media `2670`: `high_confidence_match`
- media `2654`: `low_confidence_match`
- media `2647`: `low_confidence_match`

## DB Writes

- Attempted: `False`
- Deferred until provider pilot validated: `True`
- Restore/recovery note: `DB writes are deferred until the first live provider behavior validation is reviewed.`

- `EntityEvidence`: `0`
- `MediaEntityCandidate`: `0`
- `NegativeLookupCache`: `0`
- `ProviderCache`: `0`

## Evidence / Candidate Behavior

- EntityEvidence created: `0`
- MediaEntityCandidate created: `0`
- Confirmed assignments created: `0`
- Automatic trusted Entity creation: `false`

## Privacy Scan

- Passed: `True`
- Public artifacts checked: `docs/reports/phase-4.4b1-live-rerun-saucenao-results-summary.json, docs/reports/phase-4.4b1-live-rerun-saucenao-results.md`
- Public report excludes API key, local paths, filenames, source labels, raw request payloads, raw image bytes, and unredacted provider payloads.

## Safety Confirmation

- No original upload.
- No unknown/non_anime/unapproved illustration upload.
- No full-library scan.
- No source/iCloud mutation.
- No app-managed storage mutation.
- No DB import.
- No classification.
- No AI tagging.
- No localization.
- No Entity Resolver.
- No similarity/clustering.
- No confirmed assignment.
- No media_tags mutation.
- No TagTranslation mutation.

## Decision

- Provider suitable for larger pilot: `True`
- Reason: `based_on_tiny_live_pilot_profile`
- Phase 3.9 required before scaling: `true`
- Subscription recommended now: `False`
- Subscription reason: `not_needed_for_this_5_sample_run; reassess_after_manual_review_and_before_scale`
- Subscription likely solves: `quota_or_throughput_only_not_match_quality`
- Purchase/subscription performed: `False`
