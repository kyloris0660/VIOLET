# V.I.O.L.E.T. 标签中文本地化方案

## 设计原则

### 为什么 canonical tag 保留英文？

1. **Danbooru 兼容性**：Danbooru 是全球最大的动漫标签数据库，所有标签以英文命名。保留英文 canonical name 确保与 Danbooru 生态兼容。
2. **AI 模型输出**：WDv3 模型输出的标签名称是英文的，直接与数据库中的 canonical name 对应。
3. **数据一致性**：数据库中所有 tag.name 保持英文，避免混合语言带来的搜索和匹配问题。
4. **Booru 导入兼容**：从 Danbooru/Gelbooru 导入的标签直接匹配英文名。
5. **社区通用**：英文标签在全球动漫社区通用，方便交流。

### 为什么 UI 显示中文？

1. **用户体验**：中文用户阅读中文标签更直观。
2. **降低门槛**：不需要记忆英文标签名。
3. **快速理解**：浏览图库时可以快速理解标签含义。

### 数据库 tag.name 能不能全部改成中文？

**不能。** 原因：

1. 破坏 Danbooru 搜索兼容性
2. 破坏 AI 模型输出匹配
3. 破坏 Booru 导入功能
4. 无法处理没有标准中文翻译的标签（如 `thighhighs`、`ahoge`）
5. 增加数据库迁移风险
6. 多语言用户无法使用

## 实现方案

### 翻译优先级

Phase 2.2.2 引入了多层翻译来源，优先级从高到低：

1. **手动/已审核 DB 翻译** (`source=manual`, `status=reviewed`)
2. **静态词典** (`frontend/static/data/tag_translations_zh.json`)
3. **LLM 翻译缓存** (`source=llm`, `status=translated`)
4. **Fallback 到 canonical tag**（英文原名）

### 数据库持久化翻译（Phase 2.2.2）

**数据表：** `blombooru_tag_translations`

| 字段 | 说明 |
|------|------|
| `canonical_name` | Danbooru canonical tag（英文） |
| `language` | 语言代码，如 `zh-CN` |
| `display_name` | 中文显示名 |
| `aliases_json` | 中文搜索别名 JSON 数组 |
| `category` | general/character/copyright/artist/meta |
| `source` | static/llm/manual/imported |
| `status` | pending/translated/reviewed/rejected |
| `needs_review` | 是否需要人工审核 |
| `confidence` | 置信度（可空） |
| `provider` | 翻译来源提供者 |

**启动时自动 seed：** 应用启动时会将静态 JSON 中的 79 个翻译导入 DB（`source=static`），不会覆盖已有的高优先级翻译。

### 静态翻译词典

**文件位置：** `frontend/static/data/tag_translations_zh.json`

**格式：**
```json
{
  "_meta": { "description": "...", "version": "1.0" },
  "tags": {
    "blue_eyes": "蓝眼睛",
    "long_hair": "长发",
    ...
  },
  "reverse": {
    "蓝眼睛": "blue_eyes",
    "长发": "long_hair",
    ...
  }
}
```

- `tags`：canonical name → 中文显示名
- `reverse`：中文显示名 → canonical name（用于搜索 alias）
- 仍然作为 fallback 保留，但 DB 翻译优先

### 前端显示

**文件：** `frontend/static/js/tag-localization.js`

- `TagLocalization.getDisplayName(canonicalName)` → 返回中文名或原名
- `TagLocalization.getDisplayWithCanonical(canonicalName)` → 返回"中文名 (canonical)"
- `TagLocalization.fetchBatchTranslations(names)` → 批量从后端 API 获取翻译
- 在 `media-viewer-base.js` 的 `renderTags` 中先批量预取翻译再逐个显示
- 鼠标悬停（tooltip）显示 canonical tag
- 翻译查找优先级：后端 API 缓存 → 静态 JSON → canonical

### 搜索 alias

**文件：** `backend/app/utils/search_parser.py`

- 使用 DB 翻译缓存（每 5 分钟刷新）进行中文 alias 解析
- Fallback 到静态 `tag_translations_zh.json` 的 `reverse` 映射
- 在 `parse_search_query` 中，对每个 tag token 调用 `resolve_zh_alias()`
- 用户搜索"蓝眼睛" → 转换为 `blue_eyes` → 正常查询数据库
- 不影响英文搜索和负向搜索
- Alias 冲突时按 source 优先级解决（manual > static > llm > imported）

### Fallback 策略

- 有中文翻译：显示中文名，tooltip 显示 canonical
- 无中文翻译：直接显示 canonical name（英文）
- 搜索时：先查 DB 缓存，再查静态 alias，无匹配则按英文 canonical 搜索

## 当前覆盖范围

初始词典覆盖约 80 个最常见的 Danbooru general tags，包括：

- 人数：1girl, 2girls, 1boy, solo, multiple_girls 等
- 发色：blonde_hair, black_hair, brown_hair, white_hair 等
- 瞳色：blue_eyes, green_eyes, red_eyes, brown_eyes 等
- 发型：long_hair, short_hair, twintails, ponytail 等
- 表情：smile, blush, open_mouth, closed_eyes, tears 等
- 服饰：school_uniform, dress, hat, swimsuit, kimono 等
- 场景：white_background, simple_background, outdoors, sky 等
- 动作：sitting, standing, holding, running, sleeping 等
- 风格：chibi, monochrome, comic 等

## LLM 翻译集成（Phase 2.2.2）

### 配置

在 `.env` 中配置（默认关闭）：

```
TAG_TRANSLATION_LLM_ENABLED=false
TAG_TRANSLATION_LLM_PROVIDER=openai_compatible
TAG_TRANSLATION_LLM_API_KEY=your-api-key
TAG_TRANSLATION_LLM_MODEL=gpt-4o-mini
TAG_TRANSLATION_LLM_BASE_URL=https://api.openai.com/v1
TAG_TRANSLATION_BATCH_MAX_ITEMS=50
```

### 翻译策略

- **general tag**：翻译成自然中文（如 blue_eyes → 蓝眼睛）
- **character tag**：优先给常见中文译名；不确定时保留原名，标记 `needs_review=true`
- **copyright tag**：优先作品中文名；不确定时保留原名，标记 `needs_review=true`
- **artist tag**：通常保留原名不翻译，标记 `needs_review=true`
- **meta/rating tag**：使用中文说明

### 为什么不实时调用 LLM？

- LLM API 调用有延迟（数百毫秒到数秒）
- 频繁调用增加成本
- LLM 可能不可用
- 每次页面渲染调用 LLM 会严重影响用户体验
- 正确做法：Admin 手动触发批量翻译 或 自动翻译新 tag → 结果缓存到 DB → 后续 UI 从缓存读取

### 自动翻译（Phase 2.2.2a）

当 `TAG_TRANSLATION_AUTO_ENABLED=true` 且 LLM 已启用时，新创建的 tag 会自动在后台翻译：

- 非阻塞：后台线程执行，不影响主操作
- 节流：每次最多 `TAG_TRANSLATION_AUTO_MAX_ITEMS` 个 tag
- 安全：使用独立 DB session，异常不影响主应用
- 不修改 canonical tag.name

### 覆盖规则（Phase 2.2.2a 修复）

低优先级 source 不能覆盖高优先级 source：
- `llm` 不能覆盖 `static` 或 `manual`
- `static` 不能覆盖 `manual`
- 相同 source 可以更新
- Admin 手动操作使用 `force=True` 可以强制覆盖

### Admin UI

在 Admin Panel 的「标签本地化」部分可以：
1. 查看翻译统计
2. 查看 LLM 状态（provider、model、API key 配置状态、自动翻译状态）
3. 测试 LLM 翻译连接
4. 手动编辑翻译
5. 批量 LLM 翻译（支持 dry-run）
6. 审核 LLM 翻译结果

详见 `docs/tag-localization-llm.md`。

## Character / Copyright / Artist tag 处理

- **Character tag**：角色名通常有中文通用译名（如 初音ミク → 初音未来），但也有争议性翻译
- **Copyright tag**：作品名通常有官方或通用中文译名
- **Artist tag**：艺术家名称通常保持原文不翻译
- 这三类 tag 的 LLM 翻译会自动标记 `needs_review=true`
- 手动翻译（`source=manual`, `status=reviewed`）优先级最高

### 角色名 / 中文译名 / 罗马音 / 原文名

动漫角色命名可能涉及：
- 日文原名：初音ミク
- 罗马音：Hatsune Miku  
- Danbooru canonical：hatsune_miku
- 中文通用译名：初音未来

建议方案：
- canonical tag 保持 Danbooru 英文名（hatsune_miku）
- 中文词典提供中文译名映射
- 搜索同时支持 canonical 和中文名
- 未来可添加日文名 alias

## 中文搜索与 Danbooru 风格搜索共存

当前实现：
- Danbooru 风格搜索完全保留（英文 tag、通配符、负向搜索、meta qualifier）
- 中文 alias 优先查 DB 缓存（每 5 分钟刷新），再查静态 JSON
- 两者不冲突，中文搜索是英文搜索的补充

示例：
- `blue_eyes` → 搜索 blue_eyes（英文 canonical）
- `蓝眼睛` → 转换为 blue_eyes → 搜索 blue_eyes
- `-blue_eyes` → 排除 blue_eyes
- `-蓝眼睛` → 转换为 -blue_eyes → 排除 blue_eyes
- `blue_eyes long_hair` → 同时匹配两个标签
- `蓝眼睛 长发` → 转换为 blue_eyes long_hair

## 词典维护

### 方法 1：Admin UI（推荐）

在 Admin Panel → 标签本地化 中：
1. 手动输入翻译并保存
2. 翻译立即生效（搜索缓存自动刷新）

### 方法 2：批量 LLM 翻译

1. 在 `.env` 中配置 LLM API
2. 在 Admin Panel → 标签本地化 → 批量 LLM 翻译
3. 先 dry-run 预览，确认后执行
4. 审核 LLM 结果，标记为 reviewed 或 rejected

### 方法 3：静态词典

编辑 `frontend/static/data/tag_translations_zh.json`：

1. 在 `tags` 中添加 `"canonical_name": "中文名"`
2. 在 `reverse` 中添加 `"中文名": "canonical_name"`
3. 重启应用（前端会重新加载词典，启动时会自动 seed 到 DB）

### 注意事项

- 中文名应唯一（不能有两个不同的 canonical tag 映射到同一个中文名）
- 不要修改数据库中的 tag.name
- LLM API key 不要提交到版本控制
- character/copyright/artist tag 翻译建议人工确认

## 已知限制

- 静态词典目前覆盖约 80 个最常见 general tags
- 自动翻译需要显式开启（`TAG_TRANSLATION_AUTO_ENABLED=true`）
- character/copyright/artist tag 翻译质量依赖 LLM 或人工
- 搜索 alias 缓存每 5 分钟刷新（Admin/自动翻译操作后立即刷新）
- 不支持多语言同时显示（当前只支持 zh-CN）
- 低优先级 source 不能覆盖高优先级 source
