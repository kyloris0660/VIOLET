# S3A-M2 AI Tag Assignment Incident

## Discovery

- Discovered by manual production UI validation of newly imported S3A-M2 media.
- Visible symptom: high-confidence AI media tags appeared in the suggestion grouping instead of normal category groups.

## Scope

- Affected run IDs: `[7, 8]`.
- Affected media: `349`.
- Assignments inspected: `17464`.

## Root Cause

- Root cause: manual_sync_execute_used_an_overbroad_suggestion_override; the first repair then retained an over-strict proper-noun suggestion-only policy instead of mature media-tag semantics.
- Why tests/contract missed it: previous gates counted ai_tagged media and an over-strict proper-noun suggestion safety rule, but did not assert mature assignment-level normal-vs-suggestion semantics for high-confidence visual or character/copyright/artist media tags.
- `ai_tagged` count proved assignment rows existed, but did not prove normal-vs-suggestion semantics or UI category placement.

## Repair Results

- Before high-confidence proper expected/incorrect/normal: `157` / `157` / `0`.
- Converted suggestion->normal: `157`.
- Proper-noun suggestions inspected/converted/kept: `207` / `157` / `50`.
- After high-confidence non-proper incorrect suggestions: `0`; high-confidence proper incorrect suggestions: `0`.
- Duplicate rows created: `0`; assignments deleted/replaced: `0`.
- Entity/SourceConcept truth violations: `0`.

## Cohort Audit

- Baseline selection: latest older non-S3A-M2 media with source='ai_wd' before affected cohort upload window.
- S3A-M2 cohort / baseline size: `349` / `194`.
- S3A-M2 normal/suggestion avg: `37.622` / `12.418`.
- Baseline normal/suggestion avg: `39.526` / `12.021`.
- Blocker anomalies remaining: `0`.

## UI Verification

- Status: `passed`; method: `playwright_msedge_against_launcher_started_production_server_after_second_repair`.
- Samples / normal-visible pass / mature proper-normal pass / suggestion-visible pass: `8` / `8` / `3` / `8`.
- Computer Use result: unavailable_in_current_tool_session; tool discovery did not expose computer-use controls after clean retry, fallback used Playwright/Edge

## Prevention

- Future manual execute uses mature media-tag semantics for high-confidence general/meta/rating/character/copyright/artist tags.
- Low-confidence or threshold-edge outputs may remain suggestions.
- AI-only tags still do not create SourceConcept truth, Entity truth, provider/source truth, or confirmed entity assignments.
- Tests and S3A-M2 contract now fail if high-confidence mature-policy AI tags are all suggestions or if a cohort distribution violates policy.

No private filenames, paths, content hashes, prompts, API keys, source URLs, or raw media identifiers are included in this public incident report.
