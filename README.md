# 🔧 恩特小助手 — 智能故障查询 + 对话 Agent

> 恩特能源内部工具 — 基于 RAG Agent 架构的智能助手，支持故障代码查询、自然语言对话、跨会话记忆

---

## 📋 功能概览

| 功能 | 状态 | 说明 |
|------|------|------|
| Web 页面查询 | ✅ 已上线 | http://localhost:8000 |
| 钉钉机器人查询 | ✅ 已上线 | 搜索「恩特小助手」单聊使用 |
| 精确故障代码匹配 | ✅ 已完成 | 秒回，不调 LLM |
| 语义模糊搜索 | ✅ 已完成 | 支持自然语言/名称/原因 |
| RAG Agent 智能路由 | ✅ v1.1 | LLM Function Calling 自主决策知识库检索 |
| 跨会话记忆 | ✅ v1.1 | 对话持久化，上下文感知，用户隔离 |
| **文档管理子系统** | ✅ **v1.2.1** | Web 管理页面上传/搜索/删除文档 |
| **PDF 智能切块** | ✅ **v1.2.1** | PyMuPDF 结构分析，按章节自动切块入库 |
| **存储抽象层** | ✅ **v1.2.1** | VectorStore ABC，切换 Qdrant 只需改一处 |
| **扫描 PDF OCR** | ✅ **v1.2.2** | MinerU 大模型视觉识别，扫描 PDF → Markdown → 入库 |
| **Markdown 切块** | ✅ **v1.2.2** | 按标题层级智能切块，支持中文标准章节号 |
| 云服务器部署 | ✅ v1.0 | Ubuntu 22.04 + 宝塔面板 7×24 |
| Docker 部署 | ✅ v1.1 | CPU torch 优化，镜像瘦身 |
| 局域网共享 | ✅ 已完成 | 同 WiFi 可访问 |
| PCS 参数表对接 | ✅ 已完成 | 133 条实际工程数据 |

---

## 🏗️ 系统架构

```
┌─ 用户入口 ────────────────────────────────────────┐
│  Web 页面 (localhost:8000)  钉钉单聊 @机器人        │
└──────────────────────┬────────────────────────────┘
                       │
                       ▼
┌───────────────────────────────────────────────────┐
│              RAG Agent (agent.py)                  │
│    基于 DeepSeek Function Calling，自主决策：      │
│                                                    │
│  ┌── 故障代码 (d4-1) ──→ 精确匹配（秒回，不调LLM）│
│  │                                                 │
│  ├── 自然语言/名称/原因 ──→ 调 search_kb() 工具   │
│  │      ↓ Chroma 语义搜索 → 返回格式化结果         │
│  │                                                 │
│  └── 纯聊天/无关问题 ──→ 直接 LLM 对话（不查库）  │
│                                                    │
│  所有路由前注入用户记忆 → 跨轮对话连贯              │
└──────────────────────┬────────────────────────────┘
                       │
                       ▼
┌───────────────────────────────────────────────────┐
│              Chroma 向量数据库                      │
│  模型：BAAI/bge-small-zh-v1.5                     │
│  数据：133 条故障记录（PCS参数表 遥信DI sheet）     │
└───────────────────────────────────────────────────┘
```

### 查询策略

| 输入类型 | 处理方式 | LLM? | 速度 |
|---------|---------|------|------|
| `d4-1`、`df-8`（故障代码） | 精确匹配（快速通道） | ❌ | ⚡ 毫秒 |
| `急停告警`（名称） | Agent 调 `search_kb()` | ❌ | ⚡ 毫秒 |
| `外部急停信号闭合`（原因） | Agent 调 `search_kb()` | ❌ | ⚡ 毫秒 |
| `这个逆变器报错了怎么办`（自然语言） | Agent 判断 → 调 `search_kb()` | ✅ 仅判断+提取 | ⚡ 较快 |
| `你好，今天天气怎么样`（闲聊） | Agent 直接回复 | ✅ 对话 | ⚡ 正常 |

---

## 🚀 快速开始

### 环境要求

- Python 3.10+
- pip 依赖（见下方）

### 安装依赖

```bash
pip install fastapi uvicorn chromadb sentence-transformers openpyxl httpx dingtalk-stream
```

### 配置凭证

编辑 `scripts/local_config.py`：

```python
# DeepSeek API Key（必填）
DEEPSEEK_API_KEY = "sk-xxxxxx"

# 钉钉 Stream 模式机器人凭证（可选）
DINGTALK_CLIENT_ID = "dingxxxxxx"
DINGTALK_CLIENT_SECRET = "xxxxxx"
```

### 同步知识库

```bash
python scripts/sync_kb.py
```

### 启动服务

```bash
python scripts/main.py
# → Web: http://localhost:8000
# → 钉钉搜索「恩特小助手」单聊使用
```

### Docker 部署

```bash
# 构建镜像
docker compose build

# 启动服务
docker compose up -d
```

### 局域网共享

```bash
netsh advfirewall firewall add rule name="恩特小助手 8000" dir=in action=allow protocol=TCP localport=8000
```

其他设备访问：`http://[本机IP]:8000`

---

## 📁 项目结构

```
D:\ENTAR_AGENT\
├── CLAUDE.md                          # 项目级指南（给 AI 使用）
├── README.md                          # 本文件
├── CHANGELOG.md                       # 版本更新日志
├── data/
│   ├── PCS参数表 V1.6.2.xlsx          # ⚡ 实际工程 PCS 参数表
│   └── standards/                     # 📄 标准文档（含 MinerU 提取输出）
├── knowledge_base/                    # 💾 Chroma 向量库（自动生成）
├── scripts/
│   ├── local_config.py                # 🔑 凭证配置（已 gitignore）
│   ├── main.py                        # 🚀 FastAPI 服务入口（挂载 /admin 路由）
│   ├── web_page.py                    # 🖥️ Web 页面独立组件
│   ├── sync_kb.py                     # 🔄 知识库同步（薄包装→doc_mgr）
│   ├── sync_standards.py              # 📄 标准同步脚本（薄包装→doc_mgr）
│   ├── sync_mineru.py                 # 🆕 MinerU 输出批量同步脚本
│   ├── mineru_extract.py              # 🆕 MinerU 扫描 PDF 提取工具
│   ├── doc_mgr/                       # 🆕 v2.0 文档管理子系统
│   │   ├── __init__.py                # 包导出
│   │   ├── models.py                  # 数据模型
│   │   ├── storage.py                 # ⭐ 存储抽象层（VectorStore ABC）
│   │   ├── engine.py                  # 处理引擎（process_file 统一入口）
│   │   ├── router.py                  # API 路由（/admin 下 6 个端点）
│   │   ├── views.py                   # 管理页面 HTML（三 Tab 界面）
│   │   ├── chunkers/                  # 切块器
│   │   │   ├── pymupdf_chunker.py     # ⭐ PyMuPDF 结构分析切块
│   │   │   ├── markdown_chunker.py    # 🆕 Markdown 标题层级切块
│   │   │   ├── unstructured_chunk.py  # Unstructured 备用
│   │   │   └── fallback.py            # 滑动窗口回退
│   │   └── extractors/               # 文本提取
│   │       ├── excel.py               # Excel → 故障代码
│   │       └── pdf_mupdf.py           # PyMuPDF → 文本+标准ID
│   └── skills/
│       ├── __init__.py                # 技能注册中心
│       ├── agent.py                   # 🤖 RAG Agent（核心，Function Calling）
│       ├── error_query.py             # 🔧 故障查询（搜索工具，供 Agent 调用）
│       ├── memory.py                  # 🧠 会话记忆模块
│       └── dingtalk_bot.py            # 🤖 钉钉 Stream 模式机器人
├── deploy/
│   ├── Dockerfile                     # 🐳 Docker 镜像配置
│   └── docker-compose.yml             # 🐳 Docker Compose 编排
├── docs/                              # 📄 项目文档
└── .claude/                           # Claude Code 配置
```

---

## 🤖 钉钉机器人配置

### 数据流

```
钉钉服务器
    ↑ WebSocket (Stream 模式)
    ↓
dingtalk_bot.py (ChatbotHandler.process)
    ↓
agent.py (RAG Agent — Function Calling 路由)
    ├── 精确故障代码 → error_query.match() 秒回
    ├── 语义搜索 → error_query.search_kb() → Chroma
    └── 聊天 → DeepSeek 直接回复
    ↑
memory.py（注入用户对话记忆）
    ↓
reply_markdown() / reply_text()
    ↓
钉钉单聊
```

### 部署步骤

1. **钉钉开发者后台** → 创建企业内部应用
2. **开启机器人能力** → 选择 Stream Mode
3. **复制 ClientID + ClientSecret** → 填入 `local_config.py`
4. **创建版本并发布**（管理员可设免审批）
5. **安装应用到组织**
6. **员工在钉钉搜索「恩特小助手」** → 进入单聊使用

---

## 📊 数据维护

故障代码数据存储在 `data/PCS参数表 V1.6.2.xlsx` 的「遥信（DI）」sheet 中。

- **数据行范围**：第 51 ~ 183 行
- **关键列**：故障代码、名称、地址、位地址、说明、故障原因、备注
- **故障代码格式**：d4-1、df-8、d10-1~d10-16 等

修改 Excel 后运行同步：
```bash
python scripts/sync_kb.py
```

---

## 📌 版本历史

| 版本 | 日期 | 亮点 |
|------|------|------|
| **v1.2.2** | **2026-07-14** | **扫描 PDF OCR — MinerU 大模型视觉识别 + Markdown 切块入库** |
| **v1.2.1** | **2026-07-13** | **文档管理子系统 — PDF 转换技术栈升级 + 统一上传/切块/入库** |
| v1.1 | 2026-07-09 | 记忆系统 + RAG Agent 升级 |
| v1.0 | 2026-07-09 | 第一版正式上线（故障查询 + 钉钉机器人 + Web） |

详见 [CHANGELOG.md](CHANGELOG.md)

---

## ⚠️ 已知问题

1. **短关键词语义搜索** — 极短关键词（2字以下）匹配效果可能不理想
2. **Windows GBK 编码** — 控制台输出 emoji 可能报错
3. **`__pycache__` 缓存坑** — 修改代码后旧进程可能不释放端口，需强杀 + 清缓存

---

## 🗺️ 路线图

- [x] 故障代码精确匹配（秒回）
- [x] 语义模糊搜索
- [x] RAG Agent 智能路由（v1.1）
- [x] 会话记忆（v1.1）
- [x] 钉钉机器人上线
- [x] PCS 实际工程数据接入
- [x] Docker 部署
- [x] 云服务器部署（7×24 小时在线）
- [x] **文档管理子系统（v1.2.1）**
- [x] **PDF 智能切块入库（PyMuPDF 替代 Unstructured）**
- [x] **存储抽象层**
- [x] **扫描 PDF OCR 识别（MinerU 大模型视觉，v1.2.2）**
- [ ] 钉钉小助手文件上传功能（接收文件自动入库）
- [ ] 综合测试 + 上线服务器
- [ ] Qdrant 向量库切换

---

*恩特能源 · 内部工具 · v1.2.2*
