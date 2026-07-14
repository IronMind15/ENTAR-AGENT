# 恩特小助手 更新日志

## v1.2.3（2026-07-14）

钉钉文件接收功能 + 代码结构优化：

### 新增

- **钉钉文件接收功能**（`scripts/file_handler.py`）：支持用户通过钉钉发送文件（PDF、Excel、图片等），自动下载并保存到服务器
- **文件保存目录**：`data/uploads/用户名_ID/日期/`，按用户和日期分类存储
- **System Prompt 外置**（`scripts/prompts/system_prompt.txt`）：将 LLM 提示词从代码中分离，便于维护和迭代

### 优化

- **钉钉机器人处理器**（`dingtalk_bot.py`）：支持文本、文件、图片三种消息类型
- **标准编号快速通道**（`standards_query.py`）：新增标准编号精确匹配，毫秒级响应
- **日志规范统一**（`mineru_extract.py`、`sync_mineru.py`）：所有 print 语句替换为 logging
- **搜索参数优化**（`error_query.py`、`standards_query.py`）：新增最小结果数量阈值
- **依赖说明更新**（`requirements.txt`）：补充 requests 和 MinerU 说明

### 技术栈

- 文件下载：复用 dingtalk_stream SDK 的 `get_image_download_url()` 方法，无需额外依赖
- 用户识别：通过钉钉 sender_id + sender_nick 区分不同用户

## v1.2.2（2026-07-14）

扫描 PDF OCR 技术栈升级 — MinerU 大模型视觉识别 + Markdown 切块入库：

### 新增

- **MinerU 文档提取脚本**（`scripts/mineru_extract.py`）：扫描 PDF 上传到 MinerU API → VLM 视觉识别 → 下载 Markdown/ZIP
- **MinerU 输出批量同步脚本**（`scripts/sync_mineru.py`）：扫描 `mineru_output/` 目录，批量将 MinerU 提取的 Markdown 入库到 Chroma
- **Markdown 切块器**（`doc_mgr/chunkers/markdown_chunker.py`）：基于 Markdown 标题层级（`#` `##` `###`）智能切块，支持中文标准章节号提取
- **doc_mgr 引擎支持 `.md` 文件**（`engine.py`）：新增 `_process_markdown()` 处理流程，MinerU 输出可直接入库
- **MinerU 提取结果**：9 份标准 PDF 已完成 OCR 提取（含 GB/T 34133、EN50178、IEC 60664-1 等）

### 技术栈

- OCR 路线：从传统 OCR（PaddleOCR）切换到大模型视觉识别（MinerU VLM），扫描 PDF → Markdown 质量更高
- 切块引擎：新增 MarkdownChunker，与 PyMuPDF 结构分析切块器并列，按文件类型自动选择

## v1.2.1（2026-07-13）

文档管理子系统（doc_mgr）——PDF 转换技术栈升级 + 统一上传→切块→入库：

### 新增

- **文档管理子系统**（`scripts/doc_mgr/`）：独立自洽的文档全生命周期管理模块
  - 存储抽象层（`VectorStore` ABC → `ChromaStore`，后续一行切 Qdrant）
  - PyMuPDF 结构分析切块器（替代 Unstructured，零额外依赖，对中文标准更可靠）
  - 管理 Web UI（`/admin` 路由，三 Tab：文档列表/上传/搜索测试）
  - 6 个 REST API 端点（列表/上传/搜索/删除/查询 collections/管理页面）
  - 上传文件自动保存 → 提取 → 切块 → 入库
  - 文件归属类型选择器（标准文档 / 故障代码，可扩展）
- **聊天页面新增「📂 文档管理」入口**，聊天 ↔ 管理双向导航
- **端到端增量写入**：`storage.add()` 自动跳过已存在 ID，分批写入（50/批）

### 优化

- **`sync_kb.py` / `sync_standards.py`** → 薄包装为 `process_file()` 调用层，向后兼容
- **`main.py` 统一挂载**：`app.include_router(admin_router)` 一行注册
- **PDF 切块质量**：多信号融合（章节号模式 + 字体名 + 左边界），页眉页脚自动清除
- **错误处理加固**：`list_docs` API 加 try/except，前端 fetch 加 `r.ok` 检查
- **前端安全性**：删除按钮改用 `data-*` 属性 + 事件委托，不再用内联 onclick

### 修复

- **`switchTab` 引用未声明 `event` 变量** → 改为传 `this` 参数
- **上传进度无反馈** → 2 秒后自动切换为「正在处理，请耐心等待...」

### 技术栈

- 切块引擎：PyMuPDF（已安装，零新依赖）
- 存储：Chroma（通过 `storage.py` 抽象，切换 Qdrant 只需改一处）
- 管理前端：内嵌 HTML/CSS/JS（同 `web_page.py` 模式，无模板引擎）
- 向量模型：BAAI/bge-small-zh-v1.5

## v1.1（2026-07-09）

记忆系统 + RAG Agent 升级：

### 新增

- **会话记忆模块**：用户对话记录持久化，支持跨会话上下文感知（`memory.py`）
- **用户名输入框**：Web 页面新增用户名输入，绑定对话记忆
- **RAG Agent 技能**：基于 DeepSeek Function Calling，LLM 自主决策是否查询知识库，统一故障查询 + 聊天托底（`agent.py`，替代原 `chat.py` + `error_query.py` 双技能路由）
- **搜索调参常量**：故障查询搜索参数集中管理（`K` / `n_results` / `score_threshold`）

### 优化

- **记忆去重**：防连续重复记录相同消息，节约 token
- **聊天上下文增强**：注入用户记忆，回答更贴合场景
- **Web 页面独立组件**：抽离 `web_page.py`，简化 `main.py` 入口
- **技能注册中心重构**：统一 `handle` 方法参数签名（`**kwargs`），提升可扩展性
- **Docker CPU torch**：Dockerfile 改用 CPU 版 PyTorch，省 ~13GB 镜像体积
- **查询逻辑优化**：防止重复提交，增强稳定性
- **日志清理**：钉钉机器人、增量同步脚本移除多余 print
- **用户记忆字数统计**：调试日志增加上下文字数，便于观测
- **CLAUDE.md**：新增 AI 协作规则（检查不等于修改、保持质疑主动补位）

### 修复

- **钉钉机器人处理逻辑**：提升稳定性与健壮性
- **`.pyc` 缓存坑**：修改代码后旧进程不释放端口，需强杀 + 清 `__pycache__`（开发文档已记录）

## v1.0（2026-07-09）

第一版正式上线：

- **故障查询**：PCS 故障代码精确匹配 + 语义搜索（Chroma + bge-small-zh）
- **通用聊天**：非故障问题走 DeepSeek 对话托底
- **技能注册中心**：通过 @register 装饰器注册/路由技能
- **钉钉 Stream 模式机器人**：搜索「恩特小助手」单聊使用
- **Web 页面**：FastAPI 提供 http://localhost:8000
- **云服务器部署**：Ubuntu 22.04 + 宝塔面板上线（7×24）
