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

---

## Phase 1：Local Library Scan MVP

**日期：** 2026-05-03

### 目标

支持扫描外部本地图片目录（如 Windows iCloud Photos），将支持的图片文件导入 AnimeLocalBooru gallery。

### 实际使用场景

```
C:\Users\kyloris\Pictures\iCloud Photos
```

该目录由 iCloud 持续同步，可能包含：未下载的占位文件（`.icloud`）、HEIC 图片、视频、损坏文件、重复文件。Phase 1 需要对这些情况保持稳健。

### 实现方式

**Copy Mode**：将外部目录中的图片复制到 `media/original/`，然后复用现有的 `process_and_save_media()` 流程完成导入。

选择 Copy Mode 而非 Symlink/直接引用的原因：
- Blombooru 架构要求 `media.path` 是 `BASE_DIR` 的相对路径
- `scanned_path` 导入模式有安全检查，拒绝 `ORIGINAL_DIR` 以外的路径
- 文件 serve 通过 `settings.BASE_DIR / media.path`，不支持外部绝对路径

### 新增/修改的文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/app/utils/local_library_scanner.py` | 新增 | 核心扫描逻辑：递归遍历、扩展名过滤、hash 去重、copy + import、错误隔离 |
| `backend/app/routes/admin/media.py` | 修改 | 新增 `POST /api/admin/scan-local-library` 端点，支持 JSON body 传路径 |
| `backend/app/config.py` | 修改 | 新增 `LOCAL_LIBRARY_PATHS` property，从 `.env` 解析 `|` 分隔的路径列表 |
| `example.env` | 修改 | 添加 `LOCAL_LIBRARY_PATHS` 配置示例及注释 |
| `docs/local-library-scan.md` | 新增 | 完整功能文档：配置方法、API 用法、curl/PowerShell 示例、统计字段说明 |
| `docs/local-anime-library-devlog.md` | 修改 | 本条目 |

### 关键设计决策

1. **不做数据库迁移**：原始路径暂存于 `Media.source` 字段（`file://` 前缀），复用已有列
2. **`|` 分隔多路径**：避免 `:` 和 `;` 与 Windows 驱动器号/PATH 分隔符冲突
3. **API 优先支持 JSON body**：避免 query string 传 Windows 路径的编码问题
4. **错误隔离**：单文件失败不影响其他文件扫描，failed_files 最多返回 50 条
5. **复制失败清理**：若文件已复制到 `media/original` 但 `process_and_save_media` 失败，自动清理副本

### 扫描统计字段

| 字段 | 含义 |
|------|------|
| `total_seen` | 递归遍历到的普通文件数 |
| `imported` | 成功导入 |
| `skipped_duplicate` | hash 已存在，跳过 |
| `skipped_unsupported` | 扩展名不支持 / symlink / .icloud 占位文件等 |
| `failed` | 处理失败（读取、复制、或导入异常） |
| `failed_files` | 失败详情列表（path + reason），最多 50 条 |

### 支持的格式（v1）

`.jpg`, `.jpeg`, `.png`, `.webp`, `.gif`

暂不支持：`.heic`, `.mp4`, `.webm`, `.mov`, `.bmp`, `.tiff`

### 已知限制

- 不做 AI tagging
- 不做 anime filtering
- 不做实时 watcher（仅手动触发）
- 不支持 HEIC（需要额外依赖 pillow-heif）
- Copy Mode 会占用额外磁盘空间

---

## Phase 1.5：Scan Safety & UX

**日期：** 2026-05-03

### 目标

增强 Local Library Scan 的安全性和可用性，为后续安全测试真实 iCloud Photos 目录做准备。

### 新增功能

1. **dry-run 模式**
   - `dry_run=true` 时只扫描和统计，不复制文件，不写入数据库
   - 仍然计算 MD5 hash 以检测重复
   - `imported` 字段表示"如果真实执行会导入多少"
   - 用于安全预览大型目录

2. **max_files 限制**
   - `max_files` 参数限制最多处理多少个候选文件
   - "候选文件"指通过扩展名/symlink/大小过滤的文件
   - 超出限制的文件计入 `skipped_limit`
   - dry_run 和真实扫描都支持
   - 用于安全测试真实目录的前 N 个文件

3. **Admin UI**
   - Admin Panel → Content tab 新增 Local Library Scan 区域
   - 可输入扫描路径（留空则使用 .env 配置）
   - 可设置 max_files 上限
   - 可勾选 dry-run 模式
   - 点击 Start Scan 触发扫描
   - 展示扫描结果 summary（total_seen, imported, skipped_duplicate, skipped_unsupported, skipped_limit, failed）
   - 展示 failed_files 表格（path + reason）

4. **API 改进**
   - `POST /api/admin/scan-local-library` 支持完整 JSON body：
     ```json
     {
       "paths": ["C:\\Users\\kyloris\\Pictures\\AnimeLocalBooruTest"],
       "dry_run": true,
       "max_files": 100
     }
     ```
   - 响应新增 `dry_run`、`max_files`、`skipped_limit` 字段
   - 保持向后兼容：不传 dry_run/max_files 时行为与 Phase 1 一致

### 修改的文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/app/utils/local_library_scanner.py` | 修改 | 新增 `dry_run` 和 `max_files` 参数，dry_run 跳过复制和导入，max_files 限制候选文件数 |
| `backend/app/routes/admin/media.py` | 修改 | `ScanLocalLibraryRequest` 新增 `dry_run` 和 `max_files` 字段，传递给 scanner |
| `frontend/templates/admin.html` | 修改 | Content tab 新增 Local Library Scan UI 区域 |
| `frontend/static/js/admin.js` | 修改 | 新增 `scanLocalLibrary()` 方法处理扫描请求和结果展示 |
| `docs/local-library-scan.md` | 修改 | 更新 API 文档、新增 dry-run 和 max_files 说明、新增 Admin UI 说明 |
| `docs/local-anime-library-devlog.md` | 修改 | 本条目 |
| `docs/current-handoff.md` | 修改 | 更新到 Phase 1.5 完成状态 |

### 关键设计决策

1. **dry_run 不改变现有行为**：不传 `dry_run` 参数时（默认 `false`），行为与 Phase 1 完全一致
2. **max_files 统计口径**：限制的是"候选文件"（通过扩展名过滤的文件），不是 `total_seen`。未通过过滤的文件（不支持的格式、symlink 等）不计入限制
3. **无数据库迁移**：不需要新增列或表
4. **响应增加元数据**：`dry_run`、`max_files`、`skipped_limit` 作为新字段返回，方便前端展示

### 已知限制

- Admin UI 路径输入只支持单个路径（多路径通过 .env 或直接 API 调用支持）
- 无进度条/实时反馈（扫描仍是同步的，大目录可能需要较长时间）
- 无扫描历史记录
- 不做 AI tagging
- 不做 anime filtering
- 不做实时 watcher

---

## Phase 1.6：Scan Job System / Progress / History

**日期：** 2026-05-03

### 目标

将 Local Library Scan 从同步请求升级为可靠的后台任务系统，支持进度查询、取消、历史记录，为真实 iCloud Photos 扫描和未来 AI auto tagging 打基础。

### 新增功能

1. **后台 Scan Job 系统**
   - `POST /api/admin/scan-local-library/jobs` — 创建后台扫描任务，立即返回 job_id
   - `GET /api/admin/scan-local-library/jobs/{id}` — 查询单个任务状态和进度
   - `GET /api/admin/scan-local-library/jobs` — 返回最近 20 条扫描历史
   - `POST /api/admin/scan-local-library/jobs/{id}/cancel` — 取消正在运行的任务
   - 旧同步 API `POST /api/admin/scan-local-library` 保持不变，向后兼容

2. **数据持久化**
   - 新增 `blombooru_scan_jobs` 数据库表
   - 记录 status、paths、dry_run、max_files、所有统计字段、failed_files、error_message
   - 通过 DIY 迁移函数 `migrate_add_scan_jobs_table` 创建

3. **后台执行**
   - 使用 Python `threading.Thread(daemon=True)` 执行扫描
   - 后台线程使用独立 DB session（不复用请求线程的 session）
   - 每处理 10 个文件刷新一次进度到数据库
   - 前端通过 1.5 秒轮询获取进度

4. **单任务限制**
   - 同一时间最多 1 个 pending/running/cancelling 任务
   - 重复创建返回 409 "Another scan job is already running"

5. **Cancel 支持**
   - 设置 job status 为 cancelling
   - scanner 每处理一个文件前检查 cancel flag
   - 检测到取消后停止扫描，最终 status=cancelled
   - 已导入文件保留，不回滚

6. **max_files 语义改进**
   - 只计算候选图片文件（.jpg/.jpeg/.png/.webp/.gif）
   - 不支持的格式不消耗 max_files 配额
   - 达到限制后立即停止，不继续遍历目录
   - 返回 `limit_reached=true`

7. **进度语义**
   - 有 max_files 时：progress = processed / max_files
   - 无 max_files 时：显示 indeterminate spinner + 实时统计
   - 返回字段：total_seen, processed, imported, skipped_duplicate, skipped_unsupported, failed, limit_reached

8. **Stale job recovery**
   - 应用启动时自动检查残留 pending/running/cancelling 任务
   - 标记为 interrupted，写入 error_message
   - 防止 Admin UI 永远显示 running

9. **路径安全**
   - 拒绝扫描项目内部目录：根目录、venv、data、media、storage、.git
   - 返回明确错误消息

10. **Admin UI 升级**
    - Start Scan 创建后台 job
    - Cancel 按钮取消运行中的 job
    - 进度条（有 max_files 时有百分比，无时为 indeterminate）
    - 实时统计数字
    - Scan History 表格（最近 20 条）
    - 点击历史行查看详情
    - 页面刷新后自动恢复轮询 running job

### 修改的文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/app/models.py` | 修改 | 新增 `ScanJob` 模型 |
| `backend/app/database.py` | 修改 | 新增 `migrate_add_scan_jobs_table` 迁移函数 |
| `backend/app/utils/local_library_scanner.py` | 重写 | 新增 cancel_check、progress_callback、validate_scan_paths、run_scan_job、mark_stale_jobs |
| `backend/app/routes/admin/media.py` | 修改 | 新增 4 个 job API 端点 + 路径安全检查 |
| `backend/app/main.py` | 修改 | 启动时调用 mark_stale_jobs |
| `frontend/templates/admin.html` | 修改 | 新 UI：进度条、cancel、history 表格 |
| `frontend/static/js/admin.js` | 修改 | 新增 startScanJob、cancelScanJob、loadScanHistory、轮询逻辑 |
| `docs/local-library-scan.md` | 重写 | 完整 API 文档 |
| `docs/local-anime-library-devlog.md` | 修改 | 本条目 |
| `docs/current-handoff.md` | 修改 | 更新到 Phase 1.6 |

### 数据库迁移

新增表 `blombooru_scan_jobs`（通过 `Base.metadata.create_all` + DIY 迁移函数双重保障）。不修改任何现有表。

### 已知限制

- 无 WebSocket 实时推送（使用轮询）
- 无扫描进度百分比（无 max_files 时）
- Cancel 不回滚已导入文件
- Admin UI 路径输入只支持单个路径
- 不做 AI tagging / anime filtering / watcher
