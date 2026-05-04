<p align="center">
  <img src="frontend/static/img/violet-logo.png" alt="V.I.O.L.E.T." width="120">
</p>

<h1 align="center">V.I.O.L.E.T.</h1>

<p align="center">
  <strong>Visual Image Organizer for Local Evaluation & Tagging</strong>
</p>

<p align="center">
  面向动漫/插画图像收藏的本地优先图库系统，专注于智能打标、人工审核与结构化管理。
</p>

---

## 项目目标

V.I.O.L.E.T. 基于 [Blombooru](https://github.com/mrblomblo/blombooru) 开发，旨在为个人动漫/插画收藏提供 Danbooru 风格的标签检索能力。

核心功能方向：
- 扫描本地图片目录（如 iCloud Photos 同步目录），可靠导入动漫/插画图片
- 通过 WDv3 AI 模型自动生成高质量标签
- 支持 Danbooru 风格的搜索和过滤
- 记录每个标签的来源（AI / 手动 / Booru 导入）、置信度和锁定状态
- 人工审核 AI 建议标签，手动标签始终优先于 AI
- 中文界面和中文标签显示

## 当前功能状态

| 功能 | 状态 | 说明 |
|------|------|------|
| Blombooru 核心（Gallery、上传、搜索、标签） | ✅ 完成 | 完整上游功能 |
| 本地图库扫描（Local Library Scan） | ✅ 完成 | 从外部目录导入图片 |
| dry-run & max_files 安全控制 | ✅ 完成 | 导入前预览 |
| 扫描任务进度 / 历史 / 取消 | ✅ 完成 | 后台任务 + Admin UI |
| 标签元数据基础（Tag Metadata Foundation） | ✅ 完成 | 来源追踪（source, confidence, locked, suggestion） |
| WDv3 AI 自动打标 MVP | ✅ 完成 | 手动触发，dry-run，批量，Admin UI |
| 确认标签 / 建议标签 | ✅ 完成 | 双阈值系统 |
| 手动/锁定标签保护 | ✅ 完成 | AI 不覆盖人工标签 |
| AI 标签审核 UI | ✅ 完成 | 确认 / 拒绝 / 锁定 / 删除建议标签 |
| 中文界面与本地化基础 | ✅ 完成 | UI 中文、标签中文显示、中文搜索别名 |
| 导入后自动 AI 打标 | Phase 2.3 | 可选，非阻塞 |
| 动漫/照片过滤 | Phase 3 | 区分动漫与照片 |
| 反向图片搜索 | Phase 3 | SauceNAO/IQDB 集成 |
| 角色/作品数据库 | 未来 | 外部数据补充 |
| 文件系统监控 | Phase 4 | 自动检测新文件 |

## 当前限制

- 不会在导入后自动运行 AI 打标（仅手动触发）
- 不支持动漫/照片过滤
- 不支持反向图片搜索
- 不支持来源 URL 自动识别
- 不支持完整的角色/作品数据库
- 中文标签搜索覆盖常用标签，未覆盖的标签使用英文 canonical name

## 快速开始（Windows 本地开发）

### 前置要求

- Python 3.12+
- PostgreSQL 17
- Git

### 安装

```powershell
git clone https://github.com/kyloris0660/AnimeLocalBooru.git
cd AnimeLocalBooru
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

从示例配置创建 `.env`：

```powershell
Copy-Item example.env .env
```

编辑 `.env` 设置 PostgreSQL 密码。启用 AI 打标：

```env
AI_TAGGING_ENABLED=true
```

### 启动

```powershell
.\venv\Scripts\Activate.ps1
python run.py --debug
```

打开 http://localhost:8000。首次运行会显示初始设置页面。

### 默认开发凭据

初始设置完成后：`admin` / `admin123`（仅限本地使用，切勿用于生产环境）。

## GUI 入口

| 页面 | URL | 用途 |
|------|-----|------|
| Gallery | `/` | 浏览、搜索、查看媒体 |
| 媒体详情 | `/media/{id}` | 查看单个媒体及其标签 |
| 管理面板 | `/admin` | 系统设置、内容管理、AI 打标 |
| 登录 | `/login` | 管理员认证 |

### 管理面板标签页

- **系统** — 应用设置、缓存、API 密钥、备份、更新
- **内容** — 媒体上传、本地图库扫描、AI 打标、标签管理、相册
- **统计** — 上传趋势、标签分布图表
- **账号** — 修改密码/用户名

## AI 打标

V.I.O.L.E.T. 使用 WDv3（SmilingWolf）模型预测 Danbooru 风格标签。

### 要点

- 从管理面板手动触发，不会自动运行
- 首次使用时从 HuggingFace Hub 下载模型（约 450 MB）
- 双阈值系统：标签被标记为"确认"（可搜索）或"建议"（待审核）
- 不会覆盖手动添加或锁定的标签
- 请始终先用 dry-run 测试

### 批量限制

- UI 中的"最大数量"不是无限制的
- 后端通过 `AI_TAGGING_BATCH_MAX_ITEMS` 限制最大批量（默认 10）
- 此限制防止误操作导致全库 AI 打标
- 可在 `.env` 中调整：`AI_TAGGING_BATCH_MAX_ITEMS=50`
- 不建议完全移除上限
- 大批量处理应通过后台任务、进度跟踪和取消功能完成
- Phase 2.3 将设计导入后自动打标功能，但默认关闭

详见 [AI 打标使用指南](docs/ai-tagging-usage-guide.md)。

## 安全使用建议

| 操作 | 建议 |
|------|------|
| 扫描 iCloud Photos | **务必**先 dry-run + max_files=100 |
| AI 打标 | **务必**先 dry-run 单张图片 |
| 批量 AI 打标 | 从 max_items=3-5 开始，不要直接全库 |
| 模型文件 | 不要提交到 git（.gitignore 已处理） |
| `.env` 文件 | 不要提交（包含密码） |
| 全库操作 | 仅在增量测试成功后进行 |

## 文档

| 文档 | 内容 |
|------|------|
| [AI 打标使用指南](docs/ai-tagging-usage-guide.md) | 完整使用指南和示例 |
| [AI 标签审核](docs/ai-tag-review.md) | 审核 UI 和 API 文档 |
| [AI 自动打标技术文档](docs/ai-auto-tagging.md) | 架构、API 参考、数据模型 |
| [本地图库扫描](docs/local-library-scan.md) | 扫描功能文档 |
| [标签元数据基础](docs/tag-metadata-foundation.md) | 来源追踪系统设计 |
| [标签中文本地化](docs/tag-localization-zh.md) | 中文显示和搜索方案 |
| [项目路线图](docs/project-roadmap.md) | 完整阶段计划 |
| [当前交接文档](docs/current-handoff.md) | 最新状态，用于恢复开发 |
| [开发日志](docs/local-anime-library-devlog.md) | 各阶段技术笔记 |

## 路线图

- **Phase 2.2.1** — V.I.O.L.E.T. 重命名 + 中文本地化基础（完成）
- **Phase 2.3** — 可选的导入后自动打标（后台任务，默认关闭）
- **Phase 3** — 动漫过滤 & 来源识别（SauceNAO, IQDB）
- **Phase 4** — 文件系统监控 & 定时扫描

## 技术栈

| 组件 | 技术 |
|------|------|
| 后端 | FastAPI (Python 3.12) |
| 前端 | Jinja2 + Tailwind CSS + Vanilla JS |
| 数据库 | PostgreSQL 17 |
| AI 模型 | WDv3 ONNX (SmilingWolf) via onnxruntime |
| 缓存 | Redis 7+（可选） |

## 上游归属

本项目基于 **Blombooru** 开发 — 一个自托管的媒体标签管理工具。

- 上游仓库：https://github.com/mrblomblo/blombooru
- 许可证：MIT

V.I.O.L.E.T.（原 AnimeLocalBooru）在 Blombooru 基础上扩展了本地图库扫描、AI 自动打标和标签来源追踪功能，专为动漫/插画收藏的 Danbooru 风格标签检索优化。
