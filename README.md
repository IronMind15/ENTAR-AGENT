# 🔧 恩特小助手

> **恩特能源（ENTAR）** 内部智能助手 — 基于 RAG Agent 架构，支持 PCS 故障代码查询、标准文档检索、自然语言对话、跨会话记忆

---

## 📋 目录

- [功能概览](#-功能概览)
- [快速开始](#-快速开始)
- [系统架构](#-系统架构)
- [存储结构](#-存储结构)
- [项目结构](#-项目结构)
- [部署指南](#-部署指南)
- [数据维护](#-数据维护)
- [版本历史](#-版本历史)
- [路线图](#-路线图)
- [已知问题](#-已知问题)

---

## 📋 功能概览

| 功能 | 版本 | 说明 |
|------|:----:|------|
| **Web 页面查询** | v1.0 | 浏览器访问 `http://localhost:8000` |
| **钉钉机器人查询** | v1.0 | 钉钉搜索「恩特小助手」单聊使用 |
| **故障代码精确匹配** | v1.0 | 输入 `d4-1` 等代码 → 秒回，不调 LLM |
| **语义模糊搜索** | v1.0 | 输入名称/原因/自然语言 → Chroma 检索 |
| **RAG Agent 智能路由** | v1.1 | LLM Function Calling 自主决策：查库 or 聊天 |
| **跨会话记忆** | v1.1 | 用户对话持久化，跨轮次上下文感知 |
| **文档管理子系统** | v1.2.1 | Web 管理页面上传/搜索/删除文档 |
| **PDF 智能切块** | v1.2.1 | PyMuPDF 结构分析，按章节自动切块入库 |
| **存储抽象层** | v1.2.1 | VectorStore ABC，切换 Qdrant 只需改一处 |
| **扫描 PDF OCR** | v1.2.2 | MinerU 大模型视觉识别，扫描件 → Markdown → 入库 |
| **Markdown 切块** | v1.2.2 | 按标题层级智能切块，支持中文标准章节号 |
| **钉钉文件接收** | v1.2.3 | 钉钉发送文件/图片 → 自动下载保存 |
| **标准编号快速通道** | v1.2.3 | 标准编号精确匹配，毫秒级响应 |
| **System Prompt 外置** | v1.2.3 | 提示词从代码分离到文件，便于维护 |
| **SQLite 用户存储** | v1.2.4 | 统一存储用户信息、对话记忆、权限管理 |
| **钉钉用户信息同步** | v1.2.4 | 后台同步姓名、职位、部门、管理者身份 |
| **管理页权限保护** | v1.2.4 | Web 管理页密码保护，操作权限控制 |
| **使用统计看板** | v1.2.4 | `/admin/stats` 看用户活跃度、消息趋势 |
| **Web 同步管理** | v1.2.5 | 后台手动触发文件同步 → MinerU 处理 → 入库，进度条追踪 |
| **强制重学** | v1.2.5 | 跳过查重，强制重新 MinerU 处理并入库 |
| **OSS 自动重试** | v1.2.5 | 网络超时自动重试 3 次，偶发抖动自动恢复 |
| **内容指纹同步** | v1.2.6 | SHA-256 判断真实内容变化，可靠追踪同步成功与失败状态 |
| **文档版本替换** | v1.2.6 | 新版本校验成功后再切换，失败保留旧版本可查询 |
| **MinerU 安全加固** | v1.2.6 | 分段完整性、缓存校验、安全解压和临时文件隔离 |
| 云服务器部署 | v1.0 | Ubuntu 22.04 + 宝塔面板 7×24 |
| Docker 部署 | v1.1 | CPU torch 优化，镜像瘦身 |
| 局域网共享 | ✅ | 同 WiFi 可访问 |

---

## 🚀 快速开始

### 环境要求

- Python 3.10+
- Windows / Linux 均可

本项目支持两种部署方式，按需选择。

### 方式一：本地直接部署（适合开发调试）

```bash
# 1. 安装 Python 依赖
pip install -r requirements.txt

# 2. 配置凭证
#    编辑 scripts/local_config.py，填入 DeepSeek API Key 和钉钉凭证
#    （只有 DeepSeek Key 是必填，不用钉钉可不配钉钉凭证）

# 3. 初始化知识库（首次运行必须执行）
python scripts/sync_kb.py           # 故障代码入库
python scripts/sync_standards.py    # 标准文档入库

# 4. 启动服务
python scripts/main.py

# → 浏览器访问: http://localhost:8000
# → 钉钉搜索「恩特小助手」单聊使用
```

#### 局域网共享（可选）

```bash
# 防火墙放行端口 8000
netsh advfirewall firewall add rule name="恩特小助手 8000" \
  dir=in action=allow protocol=TCP localport=8000

# 同一局域网设备访问 http://[本机IP]:8000
```

### 方式二：Docker 部署（适合服务器 7×24 运行）

```bash
# 1. 进入部署目录
cd deploy

# 2. 确认 docker-compose.yml 中的环境变量已配置
#    编辑 docker-compose.yml，填入 DEEPSEEK_API_KEY 等

# 3. 构建并启动
docker compose up -d

# 4. 首次启动后需初始化知识库（进入容器执行一次）
docker compose exec app python scripts/sync_kb.py
docker compose exec app python scripts/sync_standards.py

# 查看日志
docker compose logs -f
```

---

## 🏗️ 系统架构

### 整体数据流

```
┌─ 用户入口 ──────────────────────────────────────────┐
│  浏览器 (localhost:8000)      钉钉单聊 @恩特小助手    │
└───────────────────────┬─────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────┐
│              FastAPI 服务 (main.py)                    │
│                                                        │
│  ┌──────────────────────────────────────────────────┐ │
│  │  Agent 循环 (agent.py)                            │ │
│  │  DeepSeek Function Calling 自主决策路由：          │ │
│  │                                                    │ │
│  │  ① 用户消息 → 注入记忆 → LLM 判断意图              │ │
│  │  ② 需要查知识库 → 调工具 → 格式化结果               │ │
│  │  ③ 纯聊天 → 直接回复                                │ │
│  └──────────────────────────────────────────────────┘ │
│                         │                              │
│  ┌──────────────────────┴──────────────────────────┐  │
│  │  故障查询 (error_query.py)   标准查询 (standards_query.py) │
│  │  ┌─ 精确代码匹配（metadata 过滤）  ┌─ 标准编号精确匹配 │ │
│  │  └─ 语义搜索（Chroma）             └─ 全文语义搜索    │ │
│  └──────────────────────┬──────────────────────────┘  │
└─────────────────────────┼────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────┐
│              Chroma 向量数据库                         │
│  Collection: error_codes（133 条故障记录）            │
│  Collection: standards（标准文档切块）                 │
│  模型: BAAI/bge-small-zh-v1.5                        │
└──────────────────────────────────────────────────────┘
```

### 查询策略

| 场景 | 用户输入示例 | 处理路径 | 调 LLM? | 速度 |
|------|------------|---------|:-------:|:----:|
| 精确故障代码 | `d4-1`、`df-8` | 直接匹配 metadata | ❌ | ⚡ 毫秒 |
| 故障名称 | `急停告警` | Chroma 语义搜索 | ❌ | ⚡ 毫秒 |
| 故障原因 | `外部急停信号闭合` | Chroma 语义搜索 | ❌ | ⚡ 毫秒 |
| 自然语言查故障 | `逆变器报错了怎么办` | Agent 判断 → 提取关键词 → 搜索 | ✅ 判断+提取 | ⏳ 较快 |
| 标准编号 | `GB/T 34133` | 精确匹配 standards 库 | ❌ | ⚡ 毫秒 |
| 标准内容 | `光伏并网逆变器试验方法` | Chroma 语义搜索 | ❌ | ⚡ 毫秒 |
| 闲聊 | `你好`、`今天天气` | Agent 直接回复 | ✅ 对话 | ⚡ 正常 |

### 消息处理流水线（钉钉）

```
钉钉消息
  → dingtalk_bot.py (ChatbotHandler)
    → 同步用户信息（首次/超24h）
    → 判断消息类型：文本 / 文件 / 图片
      ├── 文本 → agent.py (Agent 循环路由)
      │              ├── 故障查询 → error_query.py
      │              ├── 标准查询 → standards_query.py
      │              └── 闲聊 → DeepSeek 直接回复
      ├── 文件 → file_handler.py 下载到 data/uploads/
      └── 图片 → file_handler.py 保存 + OCR（待完善）
    → reply_markdown() 回复到钉钉
```

---

## 💾 存储结构

| 存储内容 | 技术选型 | 管理模块 | 数据位置 | 版本管理 |
|---------|---------|---------|---------|:-------:|
| 用户资料 + 对话 + 权限 | **SQLite** | `user_store.py` | `data/user_store.db` | ❌ 自动生成 |
| 故障代码知识库 | **Chroma** + bge-small-zh | `doc_mgr/storage.py` | `knowledge_base/` | ❌ 可重生成 |
| 标准文档知识库 | **Chroma** + bge-small-zh | `doc_mgr/storage.py` | `knowledge_base/` | ❌ 可重生成 |
| 用户上传文件 | 文件系统 | `file_handler.py` | `data/uploads/` | ❌ 自动生成 |
| 源数据（Excel 参数表） | Excel | 手动维护 | `data/fault_codes/` | ✅ 进版本管理 |
| 源数据（PDF 标准文档） | PDF | 手动维护 | `data/standards/` | ✅ 进版本管理 |
| API 凭证 | Python 文件 | `local_config.py` | `scripts/local_config.py` | ❌ gitignore |

### SQLite 用户存储（`user_store.db`）

三张表结构：

| 表名 | 存储内容 | 关键字段 | 数据来源 |
|------|---------|---------|---------|
| `users` | 用户基本资料 | user_id, staff_id, nick, title, leader, department_names | 钉钉 API 自动同步 |
| `conversations` | 对话记录 | user_id, role(user/assistant), content, created_at | 用户交互时写入 |
| `permissions` | 操作权限 | can_upload, can_delete, can_manage | 管理员手动配置 |

**同步机制：** 用户首次发消息或距离上次同步超过 24h → 后台线程调用钉钉旧版 API（`oapi.dingtalk.com/topapi/v2/user/get`）拉取信息。

---

## 📁 项目结构

```
D:\ENTAR_AGENT\
│
├── README.md                      # 项目说明（本文件）
├── CLAUDE.md                      # AI 协作规则（给 Claude 用的指南）
├── CHANGELOG.md                   # 版本更新日志
│
├── scripts/                       # 📂 核心代码
│   ├── main.py                    # 🚀 FastAPI 入口（Web + 钉钉）
│   ├── web_page.py                # 🖥️ Web 页面组件
│   ├── local_config.py            # 🔑 凭证配置（已 gitignore）
│   ├── config.py                  # ⚙️ 全局配置（凭证读取 + 常量）
│   │
│   ├── sync_kb.py                 # 🔄 故障代码 → Chroma 同步
│   ├── sync_standards.py          # 🔄 标准 PDF → Chroma 同步
│   ├── sync_mineru.py             # 🔄 MinerU OCR 输出 → Chroma 同步
│   ├── mineru_extract.py          # 🔍 扫描 PDF → MinerU API → Markdown
│   │
│   ├── user_store.py              # 💾 SQLite 用户存储（单例）
│   ├── file_handler.py            # 📎 钉钉文件接收处理
│   │
│   ├── prompts/                   # 📄 外置提示词
│   │   └── system_prompt.txt      # LLM 系统提示词
│   │
│   ├── doc_mgr/                   # 📚 文档管理子系统（v1.2.1）
│   │   ├── __init__.py
│   │   ├── models.py              # 数据模型
│   │   ├── storage.py             # ⭐ 存储抽象层（VectorStore ABC → ChromaStore）
│   │   ├── engine.py              # 处理引擎（process_file 统一入口）
│   │   ├── router.py              # API 路由（/admin 端点）
│   │   ├── views.py               # 管理页面 HTML（三 Tab）
│   │   ├── chunkers/              # 切块器
│   │   │   ├── pymupdf_chunker.py     # ⭐ PyMuPDF 结构分析
│   │   │   ├── markdown_chunker.py    # Markdown 标题层级
│   │   │   ├── unstructured_chunk.py  # Unstructured 备用
│   │   │   └── fallback.py            # 滑动窗口回退
│   │   └── extractors/            # 文本提取
│   │       ├── excel.py               # Excel 解析
│   │       └── pdf_mupdf.py           # PyMuPDF 提取
│   │
│   └── skills/                    # 🧠 技能模块
│       ├── __init__.py            # 技能注册中心
│       ├── agent.py               # 🤖 RAG Agent（核心路由，Function Calling）
│       ├── error_query.py         # 🔧 故障查询工具
│       ├── standards_query.py     # 📋 标准文档查询工具
│       ├── memory.py              # 🧠 会话记忆（代理层：SQLite / JSON 回退）
│       └── dingtalk_bot.py        # 🤖 钉钉 Stream 模式机器人
│
├── data/                          # 📁 数据目录（自动生成 + 人工维护）
│   ├── fault_codes/               # 故障代码源数据
│   │   └── PCS参数表 V1.6.2.xlsx  # 实际工程 PCS 参数表（133 条）
│   ├── standards/                 # 标准文档源数据
│   │   ├── *.pdf                  # 国标/行标/国际标准 PDF
│   │   └── mineru_output/         # MinerU 提取结果（full.md + images，已 gitignore）
│   └── uploads/                   # 钉钉上传文件（已 gitignore）
│       └── mineru_output/         # 钉钉文件的 MinerU 提取结果
│
├── knowledge_base/                # 💾 Chroma 向量数据库（已 gitignore）
├── deploy/                        # 🐳 Docker 部署
│   ├── Dockerfile
│   └── docker-compose.yml
├── docs/                          # 📄 项目文档
└── .claude/                       # Claude Code 配置
```

---

## 🤖 部署指南

### 钉钉机器人部署

| 步骤 | 操作 | 说明 |
|:----:|------|------|
| 1 | 钉钉开发者后台 → 创建企业内部应用 | 需管理员权限 |
| 2 | 开启机器人能力 → 选择 **Stream Mode** | 无需公网 IP |
| 3 | 复制 ClientID + ClientSecret → 填入 `local_config.py` | |
| 4 | 版本管理与发布 → 创建版本 → 发布 | 管理员可设免审批 |
| 5 | 安装应用到组织 | 钉钉管理后台 → 应用管理 |
| 6 | 员工在钉钉搜索「**恩特小助手**」→ 单聊使用 | |

> **什么是 Stream Mode？**
> 传统 Webhook 模式需要公网 IP 让钉钉连进来；Stream Mode 是我们主动连钉钉（WebSocket 长连接），无需公网 IP、无需防火墙白名单、无需 SSL 证书。

### 云服务器部署

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置凭证（local_config.py）

# 3. 同步知识库
python scripts/sync_kb.py
python scripts/sync_standards.py

# 4. 使用 Supervisor / systemd 守护运行
python scripts/main.py
```

---

## 📊 数据维护

### 故障代码（PCS 参数表）

| 项目 | 说明 |
|------|------|
| 源文件 | `data/fault_codes/PCS参数表 V1.6.2.xlsx` |
| 数据 sheet | 遥信（DI） |
| 数据行 | 第 51 ~ 183 行（133 条故障记录） |
| 关键列 | 故障代码、名称、地址、位地址、说明、故障原因、备注 |
| 代码格式 | `d4-1`、`df-8`、`d10-1~d10-16` |
| 修改后同步 | `python scripts/sync_kb.py` |

### 标准文档

| 项目 | 说明 |
|------|------|
| 源文件目录 | `data/standards/` |
| 支持类型 | 文本 PDF（PyMuPDF 提取） / 扫描 PDF（MinerU OCR） |
| 文本 PDF 入库 | `python scripts/sync_standards.py` |
| 扫描 PDF 入库 | `python scripts/mineru_extract.py` → `python scripts/sync_mineru.py` |

---

## 📌 版本历史

| 版本 | 日期 | 亮点 |
|------|:----:|------|
| **v1.2.6** | **2026-07-21** | **文档同步一致性 — SHA-256 内容指纹 + 安全版本替换 + MinerU 安全加固** |
| **v1.2.5** | **2026-07-16** | **用户文件上传完善 — MinerU 稳定性 + Web 同步管理 + 目录重构** |
| **v1.2.4** | **2026-07-15** | **用户存储升级 SQLite — 权限系统 + 钉钉同步 + 统计看板** |
| **v1.2.3** | **2026-07-14** | **钉钉文件接收 + 标准编号快速通道 + System Prompt 外置** |
| **v1.2.2** | **2026-07-14** | **扫描 PDF OCR — MinerU 大模型视觉识别 + Markdown 切块** |
| **v1.2.1** | **2026-07-13** | **文档管理子系统 — 统一上传/切块/入库 + PyMuPDF 切块** |
| v1.1 | 2026-07-09 | 记忆系统 + RAG Agent 升级 |
| v1.0 | 2026-07-09 | 第一版正式上线（故障查询 + 钉钉机器人 + Web） |

详见 [CHANGELOG.md](CHANGELOG.md)

---

## 🗺️ 路线图

### 已完成

- [x] PCS 故障代码精确匹配 + 语义搜索（不调 LLM 秒回）
- [x] 钉钉 Stream 模式机器人上线
- [x] 跨会话记忆 + RAG Agent 智能路由
- [x] 文档管理子系统（上传/切块/搜索/删除）
- [x] 扫描 PDF OCR 识别（MinerU 大模型视觉）
- [x] 钉钉文件接收
- [x] SQLite 用户存储 + 钉钉同步 + 权限系统
- [x] Docker + 云服务器部署

### 后续规划

- [ ] **部门经验知识库** — 将工作中的维修记录、调试经验、常见问题整理入库，支持自然语言检索
- [ ] **钉钉知识库对接** — 接入钉钉自带知识库，实现企业内部文档一站式查询
- [ ] **群聊机器人** — 支持在群聊中 @机器人 查询，方便团队共享使用
- [ ] **文件自动入库** — 钉钉发送的 PDF/Excel 自动 OCR 提取 → 切块 → 入库，无需手动操作

---

## ⚠️ 已知问题

1. **短关键词语义搜索** — 2 字以下关键词匹配效果可能不理想
2. **Windows GBK 编码** — 控制台输出 emoji 可能报错
3. **`__pycache__` 缓存坑** — 修改代码后旧进程可能不释放端口，需强杀 + 清 `__pycache__`
4. **息屏断连** — 服务跑在本地电脑，息屏/睡眠会断开钉钉 WebSocket 连接
5. **OSS 网络超时** — `mineru.oss-cn-shanghai.aliyuncs.com` 偶发超时（已自动重试 3 次缓解）

---

*恩特能源（天津恩特能源科技有限公司）· 内部工具 · v1.2.6*

> 有问题或建议？钉钉联系开发团队
