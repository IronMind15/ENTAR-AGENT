# 🔧 恩特小助手

> **恩特能源（ENTAR）** 内部智能助手 — 基于 RAG Agent 架构，支持 PCS 故障代码查询、标准文档检索、自然语言对话、跨会话记忆

---

## 📋 目录

- [项目状态](#-项目状态)
- [功能概览](#-功能概览)
- [快速开始](#-快速开始)
- [系统架构](#-系统架构)
- [存储结构](#-存储结构)
- [项目结构](#-项目结构)
- [部署指南](#-部署指南)
- [数据维护](#-数据维护)
- [版本历史](#-版本历史)
- [当前待办](#-当前待办)
- [进化路线](#-进化路线)
- [已知问题](#-已知问题)

---

## 🎯 项目状态

| 项目 | 当前状态 |
|------|----------|
| 当前版本 | **v1.6.1**（2026-08-06） |
| 当前阶段 | 第一步核心能力已完成，正在进行稳定性、安全和效果验证 |
| 用户入口 | Web 页面 + 钉钉单聊机器人 |
| 知识范围 | PCS 遥信（DI）故障代码 133 条 + 标准文档 |
| 文档入库 | 管理后台手动触发；PDF、Excel、Markdown 均支持版本替换；支持按中心隔离 |
| 已验证 | 203 项自动化测试；本地 Web/钉钉功能曾完成同事实测 |
| 尚未验证 | v1.4.1 与真实 MinerU、正式知识库、钉钉生产环境的完整回归 |

> Docker 和云服务器部署配置已提供，但“配置可用”不等于“当前线上实例已验证”。发布前仍需按环境执行端到端验收。

---

## 📋 功能概览

功能按用户使用路径排列，“首次版本”表示首次引入，不代表该功能最后一次修改的版本。

| 领域 | 能力 | 首次版本 | 状态 | 说明 |
|------|------|:--------:|:----:|------|
| 用户入口 | Web 页面 | v1.0 | ✅ 可用 | 浏览器访问 `http://localhost:8000` |
| 用户入口 | 钉钉单聊机器人 | v1.0 | ✅ 已上线 | 搜索「恩特小助手」进入单聊 |
| 故障查询 | 故障代码精确匹配 | v1.0 | ✅ 可用 | 输入 `d4-1` 等代码，直接查 metadata，不调 LLM |
| 故障查询 | 混合检索（语义+BM25） | v1.3.0 | ✅ 可用 | 向量 + jieba 分词 BM25 双路召回 RRF 融合，短关键词召回明显提升 |
| 故障查询 | 重排（bge-reranker） | v1.3.0 | ✅ 已生效 | 融合后 Top-15 精排（v1.4.0 模型就位启用） |
| PCB 设计 | PCB 计算技能 | v1.4.0 | ✅ 可用 | 13 类计算：走线/2152/阻抗/差分/过孔/趋肤/信号/热/LED/分压/RC/安规/布局综合校验，全本地秒回 |
| PCB 设计 | 布局综合校验 | v1.4.2 | ✅ 可用 | 一次输入电流+铜厚+电压+材料组+可用宽度，同时输出走线+安规+空间判定，冲突时给分级设计建议 |
| 经验知识 | 经验知识库查询 | v1.4.2 | ✅ 可用 | 五段式经验条目（故障现象→排查→根因→方案→验证），Agent 工具检索；.md 上传走审核入库 |
| 经验知识 | 经验提交审核 | v1.4.2 | ✅ 可用 | .md 默认入经验库，审核口令「同意同步 id 经验库」 |
| 用户体验 | 钉钉即时反馈 | v1.4.0 | ✅ 可用 | 慢操作先回「正在处理」，出错返回错误码，秒回不打扰 |
| 用户体验 | Web 端 Markdown 渲染 + 来源展示 | v1.5.1 | ✅ 可用 | 回答格式化展示（表格/列表/代码块），底部标注数据来源 chip |
| 用户体验 | 双层会话记忆 | v1.5.3 | ✅ 可用 | 短期窗口 8 轮 + LLM 长期记忆（事实+摘要），跨会话记住用户重要信息 |
| 用户体验 | 多人并发消息处理 | v1.6.0 | ✅ 可用 | 慢操作放行线程池互不阻塞，一人问 LLM 不再卡住所有人；同人串行、异人并行；DeepSeek 并发限流 20 |
| 稳定性 | 并发修复（v1.6.1） | v1.6.1 | ✅ 可用 | 锁跨事件循环重建、回复/记忆放行线程池、懒加载单例双检锁，208 项回归全绿 |
| 故障查询 | 名称、原因语义搜索 | v1.0 | ✅ 可用 | 使用 Chroma + 中文 Embedding 检索 |
| 标准查询 | 标准编号快速匹配 | v1.2.3 | ✅ 可用 | 例如 `GB/T 34133`，不调 LLM |
| 标准查询 | 标准正文语义搜索 | v1.2.1 | ✅ 可用 | 检索 PDF 或 MinerU Markdown 切块 |
| 智能路由 | RAG Agent + 工具调用 | v1.1 | ✅ 可用 | DeepSeek 决定查故障、查标准或直接回答 |
| 智能路由 | 工具注册中心 | v1.2.7 | ✅ 可用 | @register 装饰器注册，新增工具无需改 agent.py |
| 会话能力 | 跨会话记忆 | v1.1 | ✅ 可用 | SQLite 持久化最近对话上下文 |
| 文件入口 | 钉钉文件和图片接收 | v1.2.3 | ✅ 可用 | 自动保存到 `data/uploads/`，入库仍需管理员确认 |
| 文件入口 | 上传后主动审核通知 | v1.2.7 | 🧪 待实测 | 固定审核人模式；批准后才启动同步，后续可切部门主管 |
| 文档管理 | 上传、搜索、删除、同步 | v1.2.1 | ✅ 可用 | `/admin` 统一管理文档生命周期 |
| 文档处理 | PDF/Markdown 智能切块 | v1.2.1 | ✅ 可用 | PyMuPDF 结构分析或 Markdown 标题层级切块 |
| OCR | MinerU 扫描 PDF 识别 | v1.2.2 | ✅ 可用 | 支持大 PDF 分段、重试、缓存与安全解压 |
| 同步一致性 | SHA-256 内容指纹 | v1.2.6 | ✅ 已测试 | 依据真实内容变化判断是否需要重学 |
| 同步一致性 | 安全文档版本替换 | v1.2.6 | ✅ 已测试 | 新版本校验成功后切换，失败保留旧版本 |
| 管理能力 | 多中心部门知识库 | v1.2.8 | ✅ 已实现 | 五中心隔离 + 用户多中心归属 + 审核流程嵌部门 |
| 管理能力 | 用户中心管理页面 | v1.2.8 | ✅ 可用 | `/admin` 新增「用户管理」Tab，勾选设置归属中心 | SQLite 用户存储、权限配置和 `/admin/stats` |
| 管理能力 | 修改文件归属 | v1.5.2 | ✅ 可用 | 同步管理页可改文件归属，已入库文件「强制重学」重写 Chroma |
| 部署 | 本机与局域网运行 | v1.0 | ✅ 已验证 | 同一局域网可访问，电脑休眠会导致服务中断 |
| 部署 | Docker / 云服务器 | v1.1 | 🟡 待回归 | 仓库已提供配置，v1.4.1 尚未做生产环境验收 |

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
#    DeepSeek Key 用于 Agent/聊天；不用钉钉可不配置钉钉凭证

# 3. 初始化知识库（首次运行必须执行）
python scripts/sync_kb.py           # 故障代码入库
python scripts/sync_standards.py    # 标准文档入库

# 4. 启动服务
python scripts/main.py

# → 浏览器访问: http://localhost:8000
# → 钉钉搜索「恩特小助手」单聊使用
```

#### 固定审核人主动推送（测试模式）

先启动一次服务，在钉钉里向「恩特小助手」发送 `查看我的审核ID`。把机器人返回的钉钉员工 ID 写入 `scripts/local_config.py`，再重启服务：

```python
KNOWLEDGE_REVIEW_MODE = "fixed"
KNOWLEDGE_REVIEWER_STAFF_IDS = "你的钉钉员工ID"
```

此后员工上传 PDF、Excel 或 Markdown，文件先保存到待处理区，机器人只主动通知这个固定审核人：

- `同意同步 申请编号`：按文件类型同步到建议知识库
- `同意同步 申请编号 标准库`：显式同步到标准文档库
- `同意同步 申请编号 故障库`：显式同步到故障代码库
- `拒绝同步 申请编号 原因`：保留源文件，但不进入知识库

> 配置的是 `sender_staff_id / userid`，不是机器人回调里的 unionId。当前代码和离线测试已完成，但还需要在钉钉应用权限、真实账号与副本知识库中做端到端验收。

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

# 2. 配置 scripts/local_config.py
#    docker-compose.yml 会把该文件只作为运行时配置挂载到容器

# 3. 构建并启动
docker compose up -d

# 4. 首次启动后需初始化知识库（进入容器执行一次）
docker compose exec entar-agent python scripts/sync_kb.py
docker compose exec entar-agent python scripts/sync_standards.py

# 查看日志
docker compose logs -f
```

---

## 🏗️ 系统架构

### 整体数据流

```
浏览器 / 钉钉单聊
        │
        ▼
FastAPI (main.py) → Agent 循环（agent.py）
        │
        ├─ 精确故障代码 / 标准编号 → 快速通道（不调 LLM）
        │
        └─ 其他问题 → DeepSeek Function Calling
                       ├─ tools/ 工具注册中心
                       │     ├─ search_knowledge_base（故障知识库）
                       │     ├─ search_standards（标准知识库）
                       │     └─ calc_pcb_trace（PCB 走线计算）
                       ├─ 技能：pcb_calc（12 类 PCB 计算器）
                       ├─ 增强检索 enhanced_search
                       │     ├─ 向量 + BM25 双路召回 → RRF 融合
                       │     └─ bge-reranker 重排
                       └─ 无需检索时直接回答（聊天托底）

故障/标准查询 → doc_mgr.storage → Chroma
                                ├─ error_codes（133 条故障记录）
                                └─ standards（标准文档切块）
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
      │            → knowledge_review.py 创建审核申请
      │            → dingtalk_notifier.py 主动单聊固定审核人
      │            → 审核人回复口令 → 后台同步任务
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

核心业务表结构：

| 表名 | 存储内容 | 关键字段 | 数据来源 |
|------|---------|---------|---------|
| `users` | 用户基本资料 | user_id, staff_id, nick, title, leader, department_names | 钉钉 API 自动同步 |
| `conversations` | 对话记录 | user_id, role(user/assistant), content, created_at | 用户交互时写入 |
| `permissions` | 操作权限 | can_upload, can_delete, can_manage | 管理员手动配置 |
| `sync_status` | 上传、同步与审核状态 | review_id, reviewer_staff_id, review_status, review_task_id | 文件上传及审核流程 |

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
│   ├── dingtalk_notifier.py       # 🔔 钉钉机器人主动单聊
│   ├── knowledge_review.py        # ✅ 上传审核、鉴权与同步触发
│   │
│   ├── tools/                     # 🔧 工具注册中心（v1.2.7）
│   │   ├── __init__.py            # ⭐ @register 注册 + 分发
│   │   ├── search_knowledge_base.py   # 知识库检索工具
│   │   ├── search_standards.py        # 标准检索工具
│   │   └── calc_pcb_trace.py          # PCB 走线计算工具（IPC-2221）
│   │
│   ├── prompts/                   # 📄 外置提示词
│   │   └── system_prompt.txt      # LLM 系统提示词
│   │
│   ├── doc_mgr/                   # 📚 文档管理子系统（v1.2.1）
│   │   ├── __init__.py
│   │   ├── models.py              # 数据模型
│   │   ├── storage.py             # ⭐ 存储抽象层（VectorStore ABC → ChromaStore）
│   │   ├── identity.py            # 文档稳定 ID + SHA-256 内容指纹
│   │   ├── engine.py              # 处理引擎（process_file 统一入口）
│   │   ├── router.py              # API 路由（/admin 端点）
│   │   ├── views.py               # 管理页面 HTML（三 Tab）
│   │   ├── scheduler.py           # 文件同步调度（当前默认关闭）
│   │   ├── sync_tracker.py        # SQLite 同步状态追踪
│   │   ├── task_manager.py        # 后台任务队列
│   │   ├── recovery.py            # 🆕 崩溃恢复（清理遗留 staging/retired）
│   │   ├── chunkers/              # 切块器
│   │   │   ├── base.py                # 切块器抽象接口
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
│       ├── enhanced_search.py     # 🆕 混合检索 + 重排（v1.3.0）
│       ├── pcb_calc.py            # 🆕 PCB 计算技能 12 类（v1.4.0）
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
├── tests/                         # 🧪 文档处理、版本替换与上传审核回归测试
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
| 7 | 发送 `查看我的审核ID`，配置固定审核人后重启 | 主动审核测试阶段 |

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

> 📌 **完整版本记录见 [CHANGELOG.md](CHANGELOG.md)（唯一事实源）**。此处只列最近 3 版。

| 版本 | 日期 | 亮点 |
|------|:----:|------|
| **v1.6.1** | **2026-08-06** | **并发修复 — 锁跨事件循环重建、回复/记忆放行线程池、懒加载单例双检锁（208 项回归全绿）** |
| **v1.6.0** | **2026-08-05** | **多人并发消息处理 — 慢操作放行线程池互不阻塞，同人串行异人并行 + DeepSeek 并发限流 20** |
| **v1.5.3** | **2026-08-05** | **双层会话记忆升级 — 短期窗口 8 轮 + LLM 长期记忆** |
| **v1.5.2** | **2026-08-04** | **修改文件归属 + 修复 bge-reranker 重排静默失效** |
| **v1.5.1** | **2026-08-04** | **Web 端 UI 改版 — 三块视觉统一 + Markdown 渲染 + 来源展示** |
| **v1.4.2** | **2026-08-04** | **PCB 布局综合校验模式（12→13 类计算器）+ P1 安全收尾** |
| **v1.4.1** | **2026-08-04** | **PCB 计算修复（差分/波长崩溃）+ 新增 IPC-2152/差分/安规 3 类计算器** |
| **v1.4.0** | **2026-08-03** | **PCB 设计计算技能 + 钉钉即时反馈 + rerank 生效 + 检索稳定性修复** |
| **v1.3.0** | **2026-08-03** | **检索质量升级 — 混合检索（BM25+向量）+ 重排框架** |

---

## ✅ 当前待办

> 📋 **唯一事实源：[TODO.md](TODO.md)** —— 待办清单已统一迁移到 TODO.md，本文件不再重复维护，避免多处不同步。
> 相关：进度看板 [PROGRESS.md](PROGRESS.md) · 版本记录 [CHANGELOG.md](CHANGELOG.md)

当前待办分类（完整逐条清单见 [TODO.md](TODO.md)）：
- 🔥 **P0**：真实环境验收、崩溃恢复、MinerU 部署、审核钉钉端到端验证
- ⚠️ **P1**：管理端安全收尾、Web 身份、部署安全
- 📌 **P2**：检索评测（RAGAS）、CI/可观测性
- 🎯 **第二步**：经验知识库（三步走核心目标）

---

## 🗺️ 进化路线

| 阶段 | 目标 | 关键交付 | 进入下一阶段的条件 |
|------|------|----------|--------------------|
| **阶段 A：可靠的单实例助手** | 把 v1.2.x 做稳 | 真实环境回归、安全加固、启动恢复、CI、评测基线 | 核心流程连续运行且错误可发现、可恢复 |
| **阶段 B：可运营的企业知识助手** | 从“能查”升级为“可信、可管、可持续更新” | 经验知识库、审核流、部门级 ACL、质量看板、钉钉知识库连接器 | 知识有负责人、权限可追踪、效果有指标 |
| **阶段 C：多场景 Agent 平台** | 从单一助手升级为可扩展平台 | 多产品/部门隔离、工具注册、任务编排、统一审计与成本治理 | 业务量或团队规模证明平台化收益大于维护成本 |
| **阶段 D：按规模演进基础设施** | 解决多实例和大数据量问题 | PostgreSQL、Qdrant、队列、集中监控；必要时再拆服务 | 单机 SQLite/Chroma 或单进程锁成为可量化瓶颈 |

> 不建议现在就上 Kubernetes、多 Agent 或大规模微服务。当前最有价值的进化，是先把数据安全、权限、检索效果和运维闭环做扎实。

---

## ⚠️ 已知问题

> 完整清单（开发者/AI 视角）见 [CLAUDE.md](CLAUDE.md)。此处只列使用者关心的几条：

1. **真实环境尚未回归** — 尚未对正式知识库、真实 MinerU 和钉钉生产环境做完整端到端验收
2. **短关键词检索** — 两字以内关键词效果可能不稳定（v1.3.0 混合检索已改善，仍待评测集验证）
3. **本机运行连续性** — 本地电脑休眠会断开钉钉 Stream 连接；Docker/云端生产部署状态需单独验证
4. **主动审核待实测** — 审核推送与审批口令未对真实钉钉生产环境完成端到端验证

---

*恩特能源（天津恩特能源科技有限公司）· 内部工具 · v1.5.2*

> 有问题或建议？钉钉联系开发团队
