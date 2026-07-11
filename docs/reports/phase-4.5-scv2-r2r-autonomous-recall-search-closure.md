# SCV2-R2R: Autonomous Recall and Search Closure

## 状态

- 合同状态：`partial_autonomous_closure`。
- 分支：`codex/scv2-r2r-autonomous-recall-search-closure`。
- 证据代码 SHA：`e00a7975653bce738e83ae4e355337bb902d19c2`。
- 本阶段仅处理 PR #135 的 SourceConcept source-layer 自主闭环。

## Provider 授权与执行

- 授权状态 / 范围：`approved` / `pr_135_autonomous_pair_closure`。
- 固定金额上限：`None`；仍需预算审批：`False`。
- Primary calls / fallback used：`2407` / `False`。
- Prompt / completion / total tokens：`1568585` / `175615` / `1744200`。
- 实际计费估算：`$None`。

## 候选与机器处置

- 唯一候选 pair：`3319`；执行前去重：`0`。
- Exact / stable cache reuse：`0` / `1035`。
- must_link / cannot_link / deferred_nonblocking：`1522` / `1791` / `6`。
- Unaccounted / coverage：`0` / `1.0`。
- manual_review_required_count / operator_blocking_review_count：`0 / 0`。
- 未生成 human review queue。

## 物化、约束与证据覆盖

- Materialized SourceConcept / needs_review：`1083` / `0`。
- Deferred evidence signals：`4480`。
- Indexed fallback rows：`2654`。
- Direct/transitive cannot violations：`0` / `0`。
- Review/deferred union / unknown-role materialization：`0` / `0`。

## 搜索基准

- Expanded families / seeds：`9488` / `17424`。
- Identity path：`{'matched_seed_count': 2727, 'unmatched_seed_count': 14697, 'symmetric_family_count': 7254, 'asymmetric_family_count': 2234, 'recall': 0.156508, 'average_pairwise_jaccard': 0.7252}`。
- Evidence fallback path：`{'matched_seed_count': 11236, 'unmatched_seed_count': 6188, 'symmetric_family_count': 2833, 'asymmetric_family_count': 6655, 'recall': 0.644858, 'average_pairwise_jaccard': 0.2572}`。
- False broad union seeds / indicators / unexpected media：`9186` / `9186` / `217739`。
- Identity/fallback cannot contamination：`1370` / `7398`。
- Legacy 58-seed benchmark：`{'group_count': 10, 'seed_count': 58, 'r2_baseline': {'symmetric_group_count': 0, 'unmatched_seed_count': 16, 'average_pairwise_jaccard': 0.1552}, 'identity_path': {'symmetric_group_count': 0, 'unmatched_seed_count': 51, 'average_pairwise_jaccard': 0.7725}, 'evidence_fallback_path': {'symmetric_group_count': 0, 'unmatched_seed_count': 42, 'average_pairwise_jaccard': 0.5245}, 'symmetry_improved_vs_r2': False, 'unmatched_seeds_decreased_vs_r2': False, 'average_overlap_improved_vs_r2': True}`。

## 缓存、合同与安全

- Final regeneration cache-only / provider calls：`True` / `0`。
- 固定证据 / forbidden truth unchanged：`True` / `True`。
- R2R contract / public redaction / review pack：`True` / `True` / `True`。
- 未启动 PX1-B、Provider-2、scale-up、Entity bridge、production、full-library 或 truth promotion。
