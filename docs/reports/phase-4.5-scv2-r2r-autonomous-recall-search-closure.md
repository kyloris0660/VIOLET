# SCV2-R2R: Autonomous Recall and Search Closure

## 状态

- 合同状态：`blocked_llm_approval_required`。
- 分支：`codex/scv2-r2r-autonomous-recall-search-closure`。
- 证据代码 SHA：`b50ec16026514a3e085c036164155c51fb0288aa`。
- 本轮是 cache-only dry-run；provider 未初始化、未调用。

## 固定输入与隔离

- 隔离工作数据库：`blombooru_scv2_r2r_dryrun_test_20260710`。
- 固定证据表数：`15`。
- 基线/工作副本内容一致：`True`。
- 禁止 truth 表内容一致：`True`。
- 原始值、指纹、pair payload 与本地路径仅保存在忽略的私有工件中。

## 候选与缓存覆盖

- 当前候选 pair：`3319`。
- 当前 exact-compatible / stable-compatible：`0` / `1035`。
- 全局 semantic priors：`4349`。
- 真正缺失 pair：`2284`。
- 预计 first-pass input/completion tokens：`639658` / `182720`。
- 预计 second-pass escalation：`193`。
- 预计总成本 / 请求预算：`$1.912254` / `$2.0`。

## 自治处置与物化预览

- must_link / cannot_link / deferred_nonblocking：`200` / `760` / `75`。
- 未计入 pair / coverage：`2284` / `0.311840915939`。
- manual_review_required_count：`0`。
- operator_blocking_review_count：`0`。
- manual_review_queue_generated：`False`。
- 预览物化 SourceConcept / needs_review：`1093` / `0`。
- Deferred evidence signals：`4433`；保留投影：`SourceConceptSignal projection plus private versioned pair overlay`。

## 自动搜索基准

- family / seed：`3835` / `7661`。
- identity path：`{'matched_seed_count': 3516, 'unmatched_seed_count': 4145, 'symmetric_family_count': 1464, 'asymmetric_family_count': 2371, 'recall': 0.458948, 'average_pairwise_jaccard': 0.4114}`。
- evidence fallback path：`{'matched_seed_count': 7603, 'unmatched_seed_count': 58, 'symmetric_family_count': 931, 'asymmetric_family_count': 2904, 'recall': 0.992429, 'average_pairwise_jaccard': 0.3788}`。
- cannot-linked contamination / false broad union：`0` / `0`。

## 安全与下一门禁

- 操作计数：`{'gallery_dl_calls': 0, 'provider_metadata_acquisition_calls': 0, 'pixiv_provider_calls': 0, 'ai_tagging_calls': 0, 'media_imports': 0, 'classification_calls': 0, 'localization_calls': 0, 'upstream_observation_mutations': 0, 'production_writes': 0, 'truth_path_writes': 0, 'fallback_provider_calls': 0, 'primary_provider_calls': 0}`。
- 需要单独、明确的 operator LLM 预算授权后，才能在同一 PR 继续 provider 阶段。
- 未启动 PX1-B、Provider-2、scale-up、Entity bridge、production、full-library 或 truth promotion。

## 验证

- R2R 合同通过：`True`。
- 公开脱敏通过：`True`。
- Review pack 完整性通过：`True`。
- 浏览器验证：N/A（未修改 UI）。
