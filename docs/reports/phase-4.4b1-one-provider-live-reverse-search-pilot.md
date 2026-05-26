# Phase 4.4-B1 - One-provider Live Reverse-search Pilot

Date: 2026-05-26T16:05:23+00:00

## Summary

- Status: `credential_required`
- Stop condition: `credential_required`
- Provider selected: `saucenao`
- Provider category: `saucenao_style_reverse_search`
- Credential present: `False`
- Live requests attempted: `0`
- Derived inputs generated: `0`
- DB writes attempted: `False`
- Confirmed assignments created: `0`

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
- Derived upload attempted: `0`
- Public hashes included: `false`

## Request Results

- Requests attempted: `0`
- Requests skipped: `5`

- none

## DB Writes

- Attempted: `False`
- Restore/recovery note: `No DB writes were attempted.`

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
- Public artifacts checked: `docs/reports/phase-4.4b1-one-provider-live-reverse-search-pilot-summary.json, docs/reports/phase-4.4b1-one-provider-live-reverse-search-pilot.md`
- Public report excludes API key, local paths, filenames, source labels, raw request payloads, raw image bytes, and unredacted provider payloads.

## Credential Setup

- Required credential: `SAUCENAO_API_KEY`
- Process env: `$env:SAUCENAO_API_KEY = "<your SauceNAO API key>"`
- Local `.env`: `Add SAUCENAO_API_KEY=<your SauceNAO API key> to .env (do not commit .env).`
- Verify without printing secret: `$envHas = [bool]$env:SAUCENAO_API_KEY; $dotenvHas = [bool](Select-String -Path .env -SimpleMatch "SAUCENAO_API_KEY=" -Quiet); [pscustomobject]@{SAUCENAO_API_KEY_present=($envHas -or $dotenvHas)}`
- Next rerun command: `& "$PY" scripts/run_phase44b1_one_provider_live_reverse_search_pilot.py --media-ids 2690 2687 2670 2654 2647 --execute-live --upload-derived-approved --provider-docs-verified --report-json docs/reports/phase-4.4b1-one-provider-live-reverse-search-pilot-summary.json --report-md docs/reports/phase-4.4b1-one-provider-live-reverse-search-pilot.md --local-details-json .local_manifests/phase-4.4b1-live-details.json`

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

- Provider suitable for larger pilot: `False`
- Reason: `not_evaluated_without_live_results`
- Phase 3.9 required before scaling: `true`
