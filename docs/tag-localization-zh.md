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

### 前端显示

**文件：** `frontend/static/js/tag-localization.js`

- `TagLocalization.getDisplayName(canonicalName)` → 返回中文名或原名
- `TagLocalization.getDisplayWithCanonical(canonicalName)` → 返回"中文名 (canonical)"
- 在 `media-viewer-base.js` 的 `renderTags` 中调用
- 鼠标悬停（tooltip）显示 canonical tag

### 搜索 alias

**文件：** `backend/app/utils/search_parser.py`

- 加载 `tag_translations_zh.json` 的 `reverse` 映射
- 在 `parse_search_query` 中，对每个 tag token 调用 `resolve_zh_alias()`
- 用户搜索"蓝眼睛" → 转换为 `blue_eyes` → 正常查询数据库
- 不影响英文搜索和负向搜索

### Fallback 策略

- 有中文翻译：显示中文名，tooltip 显示 canonical
- 无中文翻译：直接显示 canonical name（英文）
- 搜索时：先查中文 alias，无匹配则按英文 canonical 搜索

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

## 未来扩展

### 大规模词典导入

未来可以从 Danbooru 标签 wiki 批量导入中文翻译。步骤：

1. 从 Danbooru 导出标签列表和 wiki 页面
2. 使用翻译 API 或人工翻译
3. 格式化为 JSON 词典格式
4. 合并到现有词典文件

### Character / Copyright / Artist tag 处理

- **Character tag**：角色名通常有中文通用译名（如 初音ミク → 初音未来），但也有争议性翻译
- **Copyright tag**：作品名通常有官方或通用中文译名
- **Artist tag**：艺术家名称通常保持原文不翻译
- 建议：character/copyright tag 的中文翻译单独管理，不混入 general tag 词典

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

### 中文搜索与 Danbooru 风格搜索共存

当前实现：
- Danbooru 风格搜索完全保留（英文 tag、通配符、负向搜索、meta qualifier）
- 中文 alias 在搜索解析层面转换为英文 canonical name
- 两者不冲突，中文搜索是英文搜索的补充

示例：
- `blue_eyes` → 搜索 blue_eyes（英文 canonical）
- `蓝眼睛` → 转换为 blue_eyes → 搜索 blue_eyes
- `-blue_eyes` → 排除 blue_eyes
- `-蓝眼睛` → 转换为 -blue_eyes → 排除 blue_eyes
- `blue_eyes long_hair` → 同时匹配两个标签
- `蓝眼睛 长发` → 转换为 blue_eyes long_hair

## 词典维护

### 添加新翻译

编辑 `frontend/static/data/tag_translations_zh.json`：

1. 在 `tags` 中添加 `"canonical_name": "中文名"`
2. 在 `reverse` 中添加 `"中文名": "canonical_name"`
3. 重启应用（前端会重新加载词典，后端搜索 alias 缓存会重置）

### 注意事项

- 中文名应唯一（不能有两个不同的 canonical tag 映射到同一个中文名）
- reverse 映射必须与 tags 映射一致
- 不要修改数据库中的 tag.name
