# V.I.O.L.E.T. 开发日志

> V.I.O.L.E.T.（原 AnimeLocalBooru）— Visual Image Organizer for Local Evaluation & Tagging

## Phase 0：项目启动与原版跑通

**日期：** 2026-05-03

### 完成的工作

1. **项目克隆与整合**
   - 从 https://github.com/mrblomblo/blombooru 克隆上游 Blombooru 项目
   - 将源码整合到 V.I.O.L.E.T. 仓库中

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
   - 站点名称设为 "V.I.O.L.E.T."

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

支持扫描外部本地图片目录（如 Windows iCloud Photos），将支持的图片文件导入 V.I.O.L.E.T. gallery。

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

---

## Phase 2：Tag Metadata Foundation

**日期：** 2026-05-04

### 目标

扩展 media-tag 关联关系，新增来源（source）、置信度（confidence）、锁定（is_locked）、建议（is_suggestion）元数据，为 Phase 2.1 AI 自动标签做准备。

### 设计决策

1. **保留 SQLAlchemy Table 模式**：`blombooru_media_tags` 仍然作为 `Table` 对象使用 `secondary=` relationship，不改为 Association Object。原因：整个项目有十几处使用 `Media.tags` relationship，改为 Association Object 会导致大规模重构，违反"不破坏现有功能"原则。
2. **直接 SQL 写入 provenance**：新增列通过 tag_service.py 的 helper 函数操作（使用 PostgreSQL `ON CONFLICT DO UPDATE`），不经过 ORM relationship。
3. **现有数据 backfill**：所有旧 tag 关系迁移为 `source=manual, confidence=1.0, is_locked=true, is_suggestion=false`。
4. **搜索过滤**：wildcard 搜索和 tag count 搜索添加 `is_suggestion=false` 过滤。由于 Phase 2 不创建 suggestion tag，实际行为不变。
5. **`update_tag_counts` 排除 suggestion**：suggestion tag 不计入 `post_count`。

### 新增/修改的文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/app/models.py` | 修改 | `blombooru_media_tags` Table 新增 6 列 |
| `backend/app/database.py` | 修改 | 新增 `migrate_add_media_tags_provenance` 迁移函数 |
| `backend/app/services/tag_service.py` | 新增 | Tag provenance service：add/update/remove/query helpers |
| `backend/app/routes/media.py` | 修改 | `process_and_save_media` 和 `update_media` 使用 tag service；media detail 返回 `tag_provenance`；`update_tag_counts` 排除 suggestion |
| `backend/app/routes/booru_import.py` | 修改 | 使用 `add_booru_import_tag_to_media` |
| `backend/app/utils/search_parser.py` | 修改 | wildcard 和 tag count 搜索排除 suggestion |
| `.gitignore` | 修改 | 新增 `storage/`、`backups/` |
| `docs/tag-metadata-foundation.md` | 新增 | 完整技术文档 |
| `docs/current-handoff.md` | 修改 | 更新到 Phase 2 |
| `docs/project-roadmap.md` | 修改 | Phase 2 移到已完成 |
| `docs/local-anime-library-devlog.md` | 修改 | 本条目 |

### 数据库迁移

- 迁移函数：`migrate_add_media_tags_provenance`
- 在 `blombooru_media_tags` 表上新增 6 列
- 幂等：检查 `source` 列是否存在，已存在则跳过
- backfill：所有现有行设为 `manual/1.0/locked/confirmed`
- 新增索引：`ix_blombooru_media_tags_source`、`ix_blombooru_media_tags_is_suggestion`
- 备份要求：迁移前需要 `pg_dump` 备份

### Tag Service API

`backend/app/services/tag_service.py` 提供：

- `add_manual_tag_to_media` — 手动添加/升级 tag
- `add_manual_tags_to_media` — 批量手动添加
- `add_booru_import_tag_to_media` — Booru 导入 tag
- `add_ai_tag_to_media` — AI tag（遵守 locked/manual 优先级）
- `set_media_tags_manual` — 替换全部 tag（PATCH 更新）
- `confirm_suggestion` / `reject_suggestion` — 确认/拒绝建议
- `update_tag_provenance` — 更新 provenance 字段
- `remove_tag_from_media` — 删除 tag 关联
- `get_media_tag_provenance` — 查询 provenance 数据

### 已知限制

- Phase 2 不创建 suggestion tag，仅提供基础设施
- `Media.tags` relationship 不过滤 suggestion（安全，因为没有 suggestion）
- 没有 tag review UI
- 没有 suggestion 搜索语法
- 没有 AI 推理集成

### 下一阶段建议

**Phase 2.1 — AI Auto Tagging**：使用已有的 WDv3 tagger 自动标签，通过 `add_ai_tag_to_media()` 写入带 provenance 的 tag。

---

## Phase 2.0.1：Review Findings Hotfix

**日期：** 2026-05-04

### 目标

处理 Codex 自动 review 发现的 6 个可靠性问题，不引入新功能。

### 修复内容

1. **Provenance indexes on fresh databases**
   - 问题：`migrate_add_media_tags_provenance` 在 `source` 列存在时直接 return，导致 fresh install（`create_all` 已创建列）不创建 named indexes
   - 修复：拆分逻辑，columns 添加和 index 创建独立执行，index 始终用 `CREATE INDEX IF NOT EXISTS` 确保存在

2. **History API 不再 interrupt active jobs**
   - 问题：`GET /scan-local-library/jobs` 中调用 `mark_stale_jobs(db)` 会把当前 running 的 job 标记为 interrupted
   - 修复：移除该调用，`mark_stale_jobs` 只在应用启动时执行（`main.py` lifespan）

3. **Cancel 请求持久化 + pending job 支持**
   - 问题：`request_cancel` 只在 worker 已注册后才能设置 cancel flag；若 cancel 发生在 worker 启动前，job 仍会正常运行
   - 修复：`request_cancel` 改为无条件 pre-set flag；`run_scan_job` 启动前检查 DB status 和内存 flag，已被 cancel 则立即退出

4. **max_files 不再全量遍历目录**
   - 问题：`list(scan_dir.rglob("*"))` 预展开整个目录树；达到 limit 后内层 break 但外层继续下一个目录
   - 修复：改用 generator（不预展开）；达到 limit 后设置 `limit_reached=True` 并在外层循环也检查，立即停止

5. **paths=[] 不再 fallback 到 env**
   - 问题：`if body and body.paths:` 将空数组视为 falsy，导致静默回退到 LOCAL_LIBRARY_PATHS
   - 修复：改用 `body.paths is not None` 检查；空数组返回 400

6. **Invalid scan roots 增加 failed 计数**
   - 问题：`_record_failure` 只记录 failed_files 但不增加 `stats["failed"]` counter
   - 修复：在 `_record_failure` 调用前增加 `stats["failed"] += 1`

### 修改的文件

| 文件 | 说明 |
|------|------|
| `backend/app/database.py` | 拆分 migration 逻辑，index 创建独立于 column 添加 |
| `backend/app/routes/admin/media.py` | 移除 history API 中的 `mark_stale_jobs`；修复 paths 空数组语义 |
| `backend/app/utils/local_library_scanner.py` | 修复 cancel race、max_files generator、invalid root counter |
| `docs/local-library-scan.md` | 更新 stale recovery、cancel、paths 行为文档 |
| `docs/current-handoff.md` | 新增 Phase 2.0.1 说明 |
| `docs/local-anime-library-devlog.md` | 本条目 |

### 测试验证

- Migration 幂等：重复运行不报错
- Provenance indexes 存在
- History API 不 interrupt active job
- Cancel pre-set flag 正常工作
- max_files=3 只处理 3 个候选文件，limit_reached=true
- paths=[] 返回 400
- Invalid root failed > 0，failed_files 有记录
- Search/media list 等现有功能正常

---

## Phase 2.1：WDv3 AI Auto Tagging MVP

**日期：** 2026-05-04

### 目标

实现手动触发的 WDv3 AI 自动标签，通过 Admin UI 对导入的图片运行模型推理，自动创建和关联 tag 并记录 provenance。

### 设计决策

1. **复用现有 WDTagger**：`backend/app/services/wd_tagger.py` 已有完整的 ONNX 推理能力（单图、批量、流式），只需封装调用层
2. **新增 AI Tagging Service 层**：`ai_tagging_service.py` 负责阈值判断、tag 创建、provenance 写入的完整流程
3. **双阈值系统**：confirm_threshold（确认）和 suggestion_threshold（建议），低于 suggestion_threshold 完全忽略
4. **分类感知阈值**：character tag 阈值 (0.65) 高于 general tag (0.35)，减少角色误识别
5. **手动触发**：不自动接入 local library scan，避免误对大量非动漫图片跑模型
6. **默认使用 wd-swinv2-tagger-v3**：速度和质量的平衡选择
7. **优雅降级**：模型不可用时应用正常启动，API 返回明确错误

### 新增/修改的文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/app/services/ai_tagging_service.py` | 新增 | AI tagging 编排服务：推理 → 阈值判断 → tag 创建 → provenance 写入 |
| `backend/app/routes/admin/ai_tagging.py` | 新增 | Admin API：model-status、single tag、batch tag |
| `backend/app/routes/admin/__init__.py` | 修改 | 注册 AI tagging 路由 |
| `backend/app/config.py` | 修改 | 新增 AI_TAGGING_* 配置属性 |
| `example.env` | 修改 | 新增 AI tagging 配置示例 |
| `frontend/templates/admin.html` | 修改 | Content tab 新增 AI Auto Tagging 区域 |
| `frontend/static/js/admin.js` | 修改 | AI tagging UI 逻辑（状态检查、单图/批量触发、结果展示） |
| `.gitignore` | 修改 | 新增 *.onnx、models/、.cache/ |
| `docs/ai-auto-tagging.md` | 新增 | 完整技术文档 |
| `docs/current-handoff.md` | 修改 | 更新到 Phase 2.1 |
| `docs/project-roadmap.md` | 修改 | Phase 2.1 移到已完成 |
| `docs/local-anime-library-devlog.md` | 修改 | 本条目 |

### API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/admin/ai-tagging/model-status` | 检查模型可用性、配置 |
| POST | `/api/admin/ai-tagging/media/{id}?dry_run=bool` | 对单张图片运行 AI tagging |
| POST | `/api/admin/ai-tagging/batch` | 批量 AI tagging（支持 media_ids、max_items、dry_run） |

### 阈值配置

| 配置 | 默认值 | 说明 |
|------|--------|------|
| AI_TAGGING_ENABLED | false | 总开关 |
| AI_GENERAL_THRESHOLD | 0.35 | general tag 确认阈值 |
| AI_CHARACTER_THRESHOLD | 0.65 | character tag 确认阈值 |
| AI_RATING_THRESHOLD | 0.50 | rating tag 确认阈值 |
| AI_SUGGESTION_THRESHOLD | 0.20 | 建议阈值，低于此值忽略 |
| AI_TAGGING_BATCH_MAX_ITEMS | 10 | 批量最大数量 |
| AI_MODEL_NAME | wd-swinv2-tagger-v3 | 使用的模型 |

### 安全控制

- 默认 AI_TAGGING_ENABLED=false，需要手动开启
- batch max_items 受 AI_TAGGING_BATCH_MAX_ITEMS 限制
- dry_run 模式不写数据库
- 不自动接入 scan 流程
- manual/locked tag 不被覆盖
- 模型文件不提交到 git

### 已知限制

- 不自动在 scan 后运行 AI tagging
- 没有 tag review UI（Phase 2.2）
- 没有 suggestion 搜索语法
- 没有 anime/photo 过滤
- 首次运行需要联网下载模型文件（~350-1200 MB）
- rating tag 名称（general/sensitive/questionable/explicit）映射为 meta 类别，不修改 media.rating 字段

### 下一阶段建议

**Phase 2.2 — AI Tag Review UI**：在 media detail 中添加 suggestion 确认/拒绝按钮，suggestion 搜索语法，批量 suggestion 管理。

---

## Phase 2.1.1：文档更新 — AI Tagging Usage Guide + README Refresh

**日期：** 2026-05-04

### 目标

为 AI tagging 功能编写完整的使用指南，刷新 README 为 V.I.O.L.E.T. 项目说明，明确当前能力边界和未来路线。

### 新增/修改的文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `docs/ai-tagging-usage-guide.md` | 新增 | 完整 AI tagging 使用指南（GUI 操作、API 示例、能力边界、未来路线） |
| `README.md` | 重写 | V.I.O.L.E.T. 项目 README（保留 Blombooru upstream credit） |
| `docs/ai-auto-tagging.md` | 修改 | 添加能力边界章节、引用 usage guide、更新下一阶段建议 |
| `docs/current-handoff.md` | 修改 | 更新时间戳、引用 usage guide |
| `docs/project-roadmap.md` | 修改 | 添加 Phase 2.1.1、细化 Phase 2.2/2.3 |
| `docs/local-anime-library-devlog.md` | 修改 | 本条目 |

### 关键内容

1. **AI Tagging 能力说明**：明确 WDv3 擅长 general tags，部分 character tags，不支持 artist/source/copyright 识别
2. **GUI 手动验证流程**：启动、登录、scan、AI tagging dry-run、real write、batch、搜索验证
3. **PowerShell API 示例**：完整的 curl/PowerShell 测试脚本
4. **安全建议**：不要全量扫描 iCloud Photos、总是先 dry-run、小批量测试
5. **自动 tagging 建议**：应在 Phase 2.2 Review UI 后做，默认关闭，非阻塞，写为 suggestion
6. **README 刷新**：项目目标、当前功能、Quick Start、安全建议、文档索引、路线图

### 不包含

- 不做代码修改（纯文档）
- 不做数据库迁移
- 不接入自动 tagging
- 不做 tag review UI

---

## Phase 2.1.2：AI Tagging Session / Rollback Hotfix

**日期：** 2026-05-04

### 目标

修复 Codex 识别的两个 AI tagging 可靠性问题：跨线程 session 使用和 batch 失败级联。

### 修改的文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/app/routes/admin/ai_tagging.py` | 修改 | 不再传递 request-scoped session 到 threadpool；新增 `_single_tag_worker`/`_batch_tag_worker` 使用独立 session |
| `backend/app/services/ai_tagging_service.py` | 修改 | batch 循环中异常后 `db.rollback()`，防止 PendingRollbackError 级联 |
| `docs/ai-auto-tagging.md` | 修改 | 添加 Session Reliability 章节 |
| `docs/current-handoff.md` | 修改 | 添加 Phase 2.1.2 条目 |
| `docs/local-anime-library-devlog.md` | 修改 | 本条目 |

### Issue A — Batch failure should rollback session

**问题：** `run_ai_tagging_batch` 中单个 media 失败后不 rollback，导致 SQLAlchemy session 处于 failed state，后续所有 media 都会因 `PendingRollbackError` 失败。

**修复：** 在 batch 循环的 except 块中加入 `db.rollback()`。

### Issue B — Do not pass request DB session into run_in_threadpool

**问题：** `ai_tagging.py` 的 endpoint 将 FastAPI request-scoped session 传入 `run_in_threadpool`，Session 非线程安全。

**修复：** endpoint 不再依赖 `get_db`；新增 `_get_session()` 和 worker 函数，在 threadpool 内创建独立 session，使用后在 finally 中关闭。`model-status` endpoint 不创建 DB session。

---

## Phase 2.2：AI Tag Review UI

**日期：** 2026-05-04

### 目标

实现 AI 标签审核界面和 API，让用户可以确认、拒绝、锁定和删除 AI 生成的 suggestion tags。

### 设计决策

1. **Confirm 保留 AI 来源**：确认 suggestion 时保留 `source=ai_wd` 和原始 confidence，而非改为 `source=manual`。原因：保留标签来源追踪，方便统计分析。
2. **Reject = 删除**：当前 MVP 中 reject 直接删除 association。不持久记录拒绝决策。原因：避免数据库迁移；未来可通过 `rejected_decisions` 表改进。
3. **Manual/locked 保护**：所有 review 操作不允许误删 manual+locked tag（除非 force=true）。
4. **复用现有 tag_service**：`confirm_suggestion` 增加 `preserve_source` 参数，最小化改动。
5. **不做数据库迁移**：本阶段无新表、无新列。
6. **Admin UI 风格复用**：Review 面板复用现有 admin.html/admin.js 风格。

### 新增/修改的文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/app/routes/admin/ai_tag_review.py` | 新增 | Review API：list、confirm、reject、lock、delete、bulk |
| `backend/app/routes/admin/__init__.py` | 修改 | 注册 review 路由 |
| `backend/app/services/tag_service.py` | 修改 | `confirm_suggestion` 新增 `preserve_source` 参数 |
| `frontend/templates/admin.html` | 修改 | Content tab 新增 AI Tag Review 区域 |
| `frontend/static/js/admin.js` | 修改 | Review UI 逻辑：加载、过滤、单项/批量操作、分页 |
| `frontend/static/js/media-viewer-base.js` | 修改 | `renderTags` 区分 confirmed/suggestion 显示 |
| `docs/ai-tag-review.md` | 新增 | 完整技术文档 |

### API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/admin/ai-tags/review` | 列出待审核 suggestions |
| POST | `/api/admin/ai-tags/{media_id}/{tag_id}/confirm` | 确认一个 suggestion |
| POST | `/api/admin/ai-tags/{media_id}/{tag_id}/reject` | 拒绝（删除）一个 suggestion |
| POST | `/api/admin/ai-tags/{media_id}/{tag_id}/lock` | 锁定一个 tag |
| DELETE | `/api/admin/ai-tags/{media_id}/{tag_id}` | 删除一个 tag（manual+locked 需 force） |
| POST | `/api/admin/ai-tags/bulk` | 批量操作（confirm/reject/lock/delete） |

### 已知限制

- Reject 不持久记录，重新 AI tagging 可能再次生成同一 suggestion
- 没有 `suggestion:tag_name` 搜索语法
- 没有 undo 功能
- 不做 auto-tag after import（Phase 2.3）

### 下一阶段建议

**Phase 2.3 — Optional Auto Tagging After Import**

---

## Phase 2.2.1：V.I.O.L.E.T. Rebrand + zh-CN Localization Foundation

**日期：** 2026-05-04

### 目标

将项目正式重命名为 V.I.O.L.E.T.（Visual Image Organizer for Local Evaluation & Tagging），并为用户界面建立完整的中文本地化基础。

### 设计决策

1. **不改变内部代码结构**：Python 包名、数据库表名、API 路径均保持英文不变
2. **不改变 canonical tag**：数据库中的 tag.name 保持 Danbooru 英文名，中文仅用于 UI 显示层
3. **静态词典方案**：使用 JSON 静态文件而非数据库存储中文翻译，避免数据库迁移
4. **搜索兼容**：中文 alias 搜索在搜索解析器层面转换，不影响数据库查询
5. **渐进式覆盖**：初始词典覆盖约 80 个常用 tag，可后续扩展

### 新增/修改的文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `frontend/static/data/tag_translations_zh.json` | 新增 | 标签中文翻译词典 |
| `frontend/static/js/tag-localization.js` | 新增 | 前端标签中文显示 helper |
| `frontend/static/locales/zh-cn.json` | 修改 | 新增 Local Library Scan、AI Tagging、AI Tag Review 翻译 |
| `frontend/static/locales/en.json` | 修改 | 同步新增翻译键 |
| `frontend/templates/admin.html` | 修改 | 硬编码英文替换为 i18n 调用 |
| `frontend/templates/base.html` | 修改 | Logo 集成到导航栏 |
| `backend/app/utils/search_parser.py` | 修改 | 中文 alias 搜索支持 |
| `frontend/static/js/media-viewer-base.js` | 修改 | 标签中文显示 |
| `README.md` | 重写 | 中文 README |
| `AGENTS.md` | 修改 | 更新项目名称和说明 |
| `example.env` | 修改 | 更新注释 |
| `docs/tag-localization-zh.md` | 新增 | 标签本地化方案文档 |
| `docs/current-handoff.md` | 修改 | 更新到 Phase 2.2.1 |
| `docs/project-roadmap.md` | 修改 | 新增 Phase 2.2.1 |
| `docs/ai-auto-tagging.md` | 修改 | 更新项目名 |
| `docs/ai-tagging-usage-guide.md` | 修改 | 更新项目名 + batch 限制说明 |
| `docs/ai-tag-review.md` | 修改 | 更新项目名 |
| `docs/tag-metadata-foundation.md` | 修改 | 更新项目名 |
| `docs/local-library-scan.md` | 修改 | 更新项目名 |
| `docs/local-anime-library-devlog.md` | 修改 | 新增 Phase 2.2.1 条目 |

### 已知限制

- 中文标签搜索仅覆盖约 80 个常用 tag
- 未覆盖的 tag 使用英文 canonical name 显示
- character / copyright / artist tag 暂无中文翻译
- 不做全量 Danbooru tag 翻译（需要外部词典导入，计划后续阶段）

### 下一阶段建议

**Phase 2.2.2 — Dynamic Tag Localization / LLM Translation Cache** ✅ (已完成)

---

## Phase 2.2.2 — Dynamic Tag Localization / LLM Translation Cache

**日期**：2026-05-05
**目标**：在 Phase 2.2.1 的静态词典基础上，实现可持续的动态 tag 中文化机制

### 核心变更

1. **数据模型**：新增 `blombooru_tag_translations` 表
2. **翻译优先级**：manual/reviewed DB > static dictionary > LLM cache > canonical fallback
3. **静态 Seed**：启动时自动将 79 个静态 JSON 翻译导入 DB
4. **LLM 集成**：可选 OpenAI-compatible provider，默认关闭
5. **Admin UI**：标签本地化管理面板
6. **公共 API**：`GET /api/tags/translations/batch` 批量翻译查询
7. **搜索增强**：DB-backed 中文 alias 缓存
8. **前端优化**：批量预取翻译

详见 `docs/tag-localization-llm.md`。

---

## Phase 2.2.2a — Auto Tag Localization + Priority Hotfix

**日期**：2026-05-05

### 核心变更

1. **Priority 修复**：`upsert_translation` 严格执行 source 优先级，低优先级不能覆盖高优先级
2. **自动翻译**：新 tag 创建时自动触发 LLM 翻译（后台线程，非阻塞）
3. **LLM 增强**：Test LLM 按钮、详细状态显示（API key 配置状态、自动翻译状态）
4. **真实 LLM 验证**：通过 OpenAI-compatible API 成功翻译 tag
5. **httpx 依赖**：添加异步 HTTP 客户端

### 下一阶段建议

**Phase 2.3 — Optional Auto Tagging After Import**
