# AnimeLocalBooru 开发日志

## Phase 0：项目启动与原版跑通

**日期：** 2026-05-03

### 完成的工作

1. **项目克隆与整合**
   - 从 https://github.com/mrblomblo/blombooru 克隆上游 Blombooru 项目
   - 将源码整合到 AnimeLocalBooru 仓库中

2. **环境搭建**
   - Python 3.12.3（系统自带）
   - PostgreSQL 16（apt 安装）
   - Python venv + pip install requirements.txt
   - 无需 Docker，直接本地运行

3. **数据库配置**
   - 创建 PostgreSQL 数据库 `blombooru`
   - 用户 `postgres`，密码 `devpassword`
   - 主机 `localhost:5432`

4. **应用启动与 Onboarding**
   - `python run.py --debug` 启动，端口 8000
   - 通过 API 完成 onboarding（admin/admin123）
   - 站点名称设为 "AnimeLocalBooru"

5. **核心功能验证**

   | 功能 | 状态 | 说明 |
   |------|------|------|
   | Web UI 访问 | ✅ | http://localhost:8000 正常加载 |
   | 图片上传 | ✅ | POST /api/media/ 上传成功，缩略图自动生成 |
   | 重复检测 | ✅ | 上传重复图片时返回 "Media already exists" |
   | Tag 创建 | ✅ | bulk-create-tags API 支持 general/character/copyright/artist/meta 类别 |
   | Tag 编辑 | ✅ | PATCH /api/media/{id} 可更新 tags |
   | Tag 搜索 | ✅ | /api/search?q=hatsune_miku 返回正确结果 |
   | Tag 自动补全 | ✅ | /api/tags?search=hat 返回匹配的 tag |
   | 文件系统扫描 | ✅ | POST /api/admin/scan-media 检测到 media/original 中的新文件 |
   | Admin Panel | ✅ | 登录、管理界面正常 |
   | 缩略图生成 | ✅ | 上传时自动生成缩略图 |

### 关键代码位置

| 模块 | 路径 | 说明 |
|------|------|------|
| **Media/Post 模型** | `backend/app/models.py` | `Media` 为核心实体（即 "post"），包含 `Tag`, `TagAlias`, `TagImplication`, `Album`, `User` 等模型 |
| **上传逻辑** | `backend/app/routes/media.py` | `upload_media()` 处理文件上传，支持普通上传和分块上传 |
| **文件系统扫描** | `backend/app/utils/file_scanner.py` | `find_untracked_media()` 扫描 media/original 目录 |
| **扫描 API** | `backend/app/routes/admin/media.py` | `POST /api/admin/scan-media` 触发扫描 |
| **缩略图生成** | `backend/app/utils/thumbnail_generator.py` | PIL 图片缩略图 + OpenCV 视频首帧缩略图 |
| **Tag 模型** | `backend/app/models.py` | `Tag`, `TagAlias`, M2M 关联表 `blombooru_media_tags` |
| **Tag 搜索** | `backend/app/utils/search_parser.py` | Danbooru 风格搜索解析器 |
| **搜索 API** | `backend/app/routes/search.py` | `/api/search` 端点 |
| **Tag 管理** | `backend/app/routes/admin/tags.py` | bulk-create-tags, CSV 导入等 |
| **Auto Tagger** | `backend/app/services/wd_tagger.py` | WDv3 ONNX 模型推理（SmilingWolf） |
| **Auto Tagger API** | `backend/app/routes/ai_tagger.py` | `/api/ai-tagger` 端点 |
| **Booru 导入** | `backend/app/routes/booru_import.py` | 从 Danbooru/Gelbooru 导入 |
| **Booru 客户端** | `backend/app/services/booru/` | Danbooru/Gelbooru API 客户端 |
| **数据库** | `backend/app/database.py` | SQLAlchemy engine, 自定义迁移系统（无 Alembic） |
| **配置** | `backend/app/config.py` | Settings 类，加载 .env + data/settings.json |
| **主入口** | `backend/app/main.py` | FastAPI app, 生命周期管理, 路由注册 |
| **认证** | `backend/app/auth.py` | JWT 认证 + admin_mode cookie 机制 |
| **前端模板** | `frontend/templates/` | Jinja2 HTML 模板 |
| **静态资源** | `frontend/static/` | CSS (Tailwind), JS, themes |

### 数据库迁移方式

Blombooru 使用自定义 DIY 迁移系统（`backend/app/database.py` 中的 `check_and_migrate_schema`），不使用 Alembic。迁移函数检查列是否存在，不存在则 ALTER TABLE 添加。

### 后台任务

- asyncio 定期任务：每 15 分钟清理上传残片
- FastAPI BackgroundTasks：共享媒体缓存处理
- ThreadPoolExecutor：AI Tagger 推理并行化
- Redis（可选）：仅用于缓存，非任务队列

### Auto Tagger 状态

- WDv3 (SmilingWolf) ONNX 模型，支持多种模型变体
- 需要首次使用时下载模型文件
- 通过 `/api/ai-tagger/predict` 调用

### 已知问题/注意事项

1. `bulk-create-tags` API 需要 `[{"name": "tag_name", "category": "general"}]` 格式，不是简单字符串数组
2. 认证需要同时提供 JWT token（Authorization header 或 access_token cookie）和 admin_mode=true cookie
3. rating 枚举值必须是 `safe`, `questionable`, `explicit`（不是缩写 s/q/e）

### 下一阶段建议（Phase 1 - 待确认）

基于用户需求和 Blombooru 已有功能分析，建议的开发路线：

1. **本地文件夹扫描增强**（优先级高）
   - Blombooru 已有 `find_untracked_media()` 扫描 `media/original`
   - 需要：支持配置外部文件夹路径，支持递归扫描子目录
   - 需要：扫描后自动导入（当前扫描只返回列表，需手动导入）

2. **自动 Tag 生成集成**（优先级高）
   - Blombooru 已有 WDv3 auto tagger
   - 需要：导入时自动调用 auto tagger
   - 需要：记录 tag 来源（AI/手动）和置信度
   - 需要：手动 tag 优先，AI tag 不覆盖手动 tag

3. **非动漫图片过滤**（优先级中）
   - 可以利用 WDv3 模型的置信度阈值来判断
   - 或引入额外的分类模型

4. **Tag 来源和置信度扩展**（优先级中）
   - 需要扩展 Tag-Media 关联表，增加 source 和 confidence 字段
   - 涉及数据库迁移

5. **后续功能**（优先级低，第一阶段不涉及）
   - 反向搜图
   - 来源补全
   - 重复图/相似图检测
   - 角色聚类
