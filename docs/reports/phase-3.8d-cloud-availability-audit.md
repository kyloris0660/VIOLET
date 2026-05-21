# Phase 3.8d-I1 Cloud Availability Audit

## Summary

- Status: `passed`
- Mode: `cloud_availability_metadata_audit`
- Manifest label: `phase-3.8c-medium-candidate-manifest.csv`
- Target label: `phase_3_8d_partial_staging`
- Read probe enabled: `False`
- Selected total: `1000`
- Already copied in partial staging: `97`
- Not yet copied: `903`
- Likely cloud placeholder count: `613`
- Copy gate: `blocked_requires_hydration_policy`

## Cloud Attribute Counts

- Exists: `1000`
- Missing: `0`
- Stat errors: `0`
- Offline: `0`
- Reparse point: `0`
- Recall on open: `0`
- Recall on data access: `613`
- Pinned: `6`
- Unpinned: `6`
- Sparse file: `0`

## Distributions

- Selected by bucket: `{"b01": 63, "b02": 63, "b03": 63, "b04": 63, "b05": 63, "b06": 63, "b07": 63, "b08": 63, "b09": 62, "b10": 62, "b11": 62, "b12": 62, "b13": 62, "b14": 62, "b15": 62, "b16": 62}`
- Risky by bucket: `{"b02": 24, "b03": 63, "b04": 62, "b05": 63, "b06": 62, "b07": 61, "b08": 53, "b09": 12, "b10": 14, "b11": 30, "b12": 51, "b13": 27, "b14": 27, "b15": 42, "b16": 22}`
- Selected by extension: `{".gif": 1, ".jpeg": 10, ".jpg": 816, ".png": 173}`
- Risky by extension: `{".jpeg": 4, ".jpg": 514, ".png": 95}`

## First Risky Examples

- `source_row_0098.jpg` bucket `b02` extension `.jpg` state `{"attribute_names": ["archive", "recall_on_data_access"], "attributes_hex": "0x00400020", "attributes_raw": 4194336, "error_code": null, "error_message": null, "exists": true, "is_file": true, "likely_cloud_placeholder": true, "offline": false, "pinned": false, "recall_on_data_access": true, "recall_on_open": false, "reparse_point": false, "sparse_file": false, "supported_platform": true, "unpinned": false}`
- `source_row_0099.png` bucket `b02` extension `.png` state `{"attribute_names": ["archive", "recall_on_data_access"], "attributes_hex": "0x00400020", "attributes_raw": 4194336, "error_code": null, "error_message": null, "exists": true, "is_file": true, "likely_cloud_placeholder": true, "offline": false, "pinned": false, "recall_on_data_access": true, "recall_on_open": false, "reparse_point": false, "sparse_file": false, "supported_platform": true, "unpinned": false}`
- `source_row_0100.png` bucket `b02` extension `.png` state `{"attribute_names": ["archive", "recall_on_data_access"], "attributes_hex": "0x00400020", "attributes_raw": 4194336, "error_code": null, "error_message": null, "exists": true, "is_file": true, "likely_cloud_placeholder": true, "offline": false, "pinned": false, "recall_on_data_access": true, "recall_on_open": false, "reparse_point": false, "sparse_file": false, "supported_platform": true, "unpinned": false}`
- `source_row_0101.jpg` bucket `b02` extension `.jpg` state `{"attribute_names": ["archive", "recall_on_data_access"], "attributes_hex": "0x00400020", "attributes_raw": 4194336, "error_code": null, "error_message": null, "exists": true, "is_file": true, "likely_cloud_placeholder": true, "offline": false, "pinned": false, "recall_on_data_access": true, "recall_on_open": false, "reparse_point": false, "sparse_file": false, "supported_platform": true, "unpinned": false}`
- `source_row_0102.jpg` bucket `b02` extension `.jpg` state `{"attribute_names": ["archive", "recall_on_data_access"], "attributes_hex": "0x00400020", "attributes_raw": 4194336, "error_code": null, "error_message": null, "exists": true, "is_file": true, "likely_cloud_placeholder": true, "offline": false, "pinned": false, "recall_on_data_access": true, "recall_on_open": false, "reparse_point": false, "sparse_file": false, "supported_platform": true, "unpinned": false}`
- `source_row_0103.jpg` bucket `b02` extension `.jpg` state `{"attribute_names": ["archive", "recall_on_data_access"], "attributes_hex": "0x00400020", "attributes_raw": 4194336, "error_code": null, "error_message": null, "exists": true, "is_file": true, "likely_cloud_placeholder": true, "offline": false, "pinned": false, "recall_on_data_access": true, "recall_on_open": false, "reparse_point": false, "sparse_file": false, "supported_platform": true, "unpinned": false}`
- `source_row_0105.jpg` bucket `b02` extension `.jpg` state `{"attribute_names": ["archive", "recall_on_data_access"], "attributes_hex": "0x00400020", "attributes_raw": 4194336, "error_code": null, "error_message": null, "exists": true, "is_file": true, "likely_cloud_placeholder": true, "offline": false, "pinned": false, "recall_on_data_access": true, "recall_on_open": false, "reparse_point": false, "sparse_file": false, "supported_platform": true, "unpinned": false}`
- `source_row_0106.png` bucket `b02` extension `.png` state `{"attribute_names": ["archive", "recall_on_data_access"], "attributes_hex": "0x00400020", "attributes_raw": 4194336, "error_code": null, "error_message": null, "exists": true, "is_file": true, "likely_cloud_placeholder": true, "offline": false, "pinned": false, "recall_on_data_access": true, "recall_on_open": false, "reparse_point": false, "sparse_file": false, "supported_platform": true, "unpinned": false}`
- `source_row_0108.jpg` bucket `b02` extension `.jpg` state `{"attribute_names": ["archive", "recall_on_data_access"], "attributes_hex": "0x00400020", "attributes_raw": 4194336, "error_code": null, "error_message": null, "exists": true, "is_file": true, "likely_cloud_placeholder": true, "offline": false, "pinned": false, "recall_on_data_access": true, "recall_on_open": false, "reparse_point": false, "sparse_file": false, "supported_platform": true, "unpinned": false}`
- `source_row_0110.jpg` bucket `b02` extension `.jpg` state `{"attribute_names": ["archive", "recall_on_data_access"], "attributes_hex": "0x00400020", "attributes_raw": 4194336, "error_code": null, "error_message": null, "exists": true, "is_file": true, "likely_cloud_placeholder": true, "offline": false, "pinned": false, "recall_on_data_access": true, "recall_on_open": false, "reparse_point": false, "sparse_file": false, "supported_platform": true, "unpinned": false}`

## Gate Rule

If `likely_cloud_placeholder_count > 0`, direct staging copy is blocked until a controlled hydrate/read-probe/backfill policy is explicitly enabled and passes.

## Privacy

- Passed: `True`
- Leaks: `[]`
- Local details artifact: `local_cloud_availability_details`

## Safety

- Metadata-only by default.
- No content read unless `--read-probe` is explicitly provided.
- No source/iCloud mutation.
- No staging cleanup/delete.
- No DB import.
