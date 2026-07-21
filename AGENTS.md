# AGENTS.md — 恩特能源 AI Agent 项目

## 🤖 与 AI 协作规则

1. **始终使用中文回答** — 无论用户输入什么语言，均以中文回复
2. **回答格式约定** — 每次回答开头用一个 emoji 表情，结尾加上「嘿嘿」
3. **检查不等于修改** — 当用户要求查问题/审代码时，执行检查并报告发现，但不要顺手改掉代码。用户可能只是想理解原理，不一定真有 bug
4. **保持质疑，主动补位** — 用户的想法不一定绝对正确，可以提出质疑和不同角度。同时主动补充用户可能不了解的原理、背景知识和代码常识，帮用户把认知盲区补上
5. **打标签前先更新 CHANGELOG 和 README** — 当用户要提交版本/打 tag 时，先检查 CHANGELOG.md 是否已包含当前版本的完整变更记录，README.md 是否反映了最新功能状态，没有则先更新再打 tag
6. **主动提醒版本更新** — 每次完成实质性改动（新功能、修 bug、改配置等）后，主动问用户是否要更新版本号，并推荐升哪一位（major/minor/patch），同时附上一句话说明理由

## 项目概述

恩特能源（天津恩特能源科技有限公司，Tianjin Entar Energy Technology Co., Ltd.，品牌 ENTAR）AI Agent 项目。目标是搭建面向中小企业的 AI Agent 全生命周期管理平台。

### 当前进度：三步走计划 - 第一步

**第一步：故障代码智能查询 + 通用聊天（已完成 MVP，托底，v1.2.3 全面完善）**
- 基于 RAG（检索增强生成）架构的内部工具
- 数据源：PCS参数表 V1.6.2.xlsx → 遥信（DI）sheet（133 条故障记录）+ 标准 PDF 文档（MinerU OCR 识别入库）
- 两级查询策略：
  - 精确匹配故障代码（d4-1 等）→ 直接返回（不调 LLM，秒回）
  - 语义搜索名称/原因/自然语言 → Chroma 检索 → 返回格式化结果
- 聊天托底：非故障自然语言问题 → DeepSeek 直接回答（agent.py 统一路由 Agent 循环）
- 路由逻辑：故障代码/关键词 → error_query，否则 → 走 Agent 循环（main.py + dingtalk_bot.py 统一）
- ✅ Web 页面可访问（http://localhost:8000）
- ✅ 钉钉 Stream 模式机器人已上线（搜索「恩特小助手」进入单聊）
- ✅ 文档管理子系统（doc_mgr，v1.2.1）：统一上传→切块→入库，Web 管理页面（/admin）
- ✅ 扫描 PDF OCR 识别（MinerU，v1.2.2）：大模型视觉识别 → Markdown → Chroma 入库
- ✅ 钉钉文件接收（v1.2.3）：用户发送文件/图片到钉钉，自动下载保存
- ✅ 局域网共享（防火墙放行端口 8000）
- ✅ 同事实测通过

## 项目结构

```
D:\ENTAR_AGENT\
├── AGENTS.md                                    # ← 项目级指南（本文件）
├── CHANGELOG.md                                 # 版本变更日志
├── README.md                                    # 项目说明
├── .gitignore                                   # Git 忽略规则
├── requirements.txt                             # Python 依赖清单
│
├── data/                                        # 📁 数据目录
│   ├── fault_codes/                             #    故障代码 Excel 数据
│   │   └── PCS参数表 V1.6.2.xlsx                #    实际工程 PCS 参数表（133 条故障）
│   ├── standards/                               #    标准文档 PDF 文件（含 MinerU 提取输出）
│   ├── uploads/                                 #    钉钉文件接收保存目录（自动生成，已 gitignore）
│   └── chat_memory.json                         #    会话记忆（自动生成，已 gitignore）
│
├── knowledge_base/                              # 💾 Chroma 向量数据库（自动管理，不退版本）
│
├── scripts/                                     # 📂 核心 Python 代码
│   ├── config.py                                #   配置模块
│   ├── local_config.py                          # 🔑 API Key / 钉钉凭证（已 gitignore）
│   ├── main.py                                  # 🚀 统一入口（FastAPI + 钉钉机器人，挂载 /admin）
│   ├── sync_kb.py                               # 🔄 Excel → Chroma 同步脚本（薄包装→doc_mgr）
│   ├── sync_standards.py                        # 📄 标准 PDF → Chroma 同步脚本（薄包装→doc_mgr）
│   ├── sync_mineru.py                           # 📄 MinerU 输出批量同步脚本（v1.2.2）
│   ├── mineru_extract.py                        # 📄 MinerU 扫描 PDF 提取工具（v1.2.2）
│   ├── file_handler.py                          # 📄 钉钉文件接收处理（v1.2.3）
│   ├── web_page.py                              # 🌐 网页处理脚本
│   ├── prompts/                                 # 📄 外置提示词目录（v1.2.3）
│   │   └── system_prompt.txt                    #    系统提示词
│   ├── doc_mgr/                                 # 🆕 v1.2.1 文档管理子系统
│   │   ├── __init__.py, models.py, storage.py   # ⭐ 存储抽象层
│   │   ├── engine.py, router.py, views.py       #    引擎 + API + 管理界面
│   │   ├── chunkers/                            #    切块器（PyMuPDF 结构分析 + Markdown 标题层级）
│   │   │   ├── pymupdf_chunker.py               #    PyMuPDF 结构分析切块
│   │   │   ├── markdown_chunker.py              # 🆕 Markdown 标题层级切块（v1.2.2）
│   │   │   ├── unstructured_chunk.py            #    Unstructured 备用
│   │   │   └── fallback.py                      #    滑动窗口回退
│   │   └── extractors/                          #    文本提取（Excel + PyMuPDF）
│   └── skills/
│       ├── __init__.py
│       ├── agent.py                             # 🤖 Agent 循环路由（聊天托底）
│       ├── dingtalk_bot.py                      # 🤖 钉钉 Stream 模式机器人
│       ├── error_query.py                       # 🔧 故障查询（两级查询策略）
│       ├── memory.py                            # 💭 会话记忆管理
│       └── standards_query.py                   # 📋 标准文档查询
│
├── docs/                                        # 📄 全部文档集中管理
│   ├── 需求文档-恩特小助手文档制作.md             # 需求文档
│   ├── 恩特小助手使用说明_20260706_151451.docx   # 使用说明（面向钉钉用户）
│   ├── 恩特小助手设计说明.docx                   # 设计说明（面向开发者）
│   ├── 恩特小助手-PCS故障查询系统架构.pptx        # 架构图
│   ├── 恩特小助手介绍页.html                     # 介绍页 HTML
│   ├── 恩特小助手架构图.drawio                   # 架构图源文件
│   ├── 恩特小助手架构图.html                     # 架构图 HTML 导出
│   ├── 标准查询功能设计文档.md                    # 标准查询功能设计
│   ├── git-commands.md                           # Git 常用命令备忘
│   ├── tech-stack-overview.md                    # 技术栈概览
│   ├── AI_Agent_Workflow_Research_Report.md     # 深度调研报告
│   └── error_query_README_旧版归档.md           # 旧版 README（历史参考）
│
├── deploy/                                      # 🐳 Docker 部署配置
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── pack.sh
│
└── .Codex\                                     # Codex 配置
```

## 技术架构

```
┌─ 用户入口 ────────────────────────────────────────┐
│  Web 页面 (http://localhost:8000)  钉钉单聊 @机器人 │
│         │                              │          │
└─────────┼──────────────────────────────┼──────────┘
          ▼                              ▼
┌───────────────────────────────────────────────────┐
│              FastAPI 服务 (main.py)                 │
│              error_query.handle(q)                 │
│                                                    │
│    ┌── 提取到故障代码（d4-1 等）──┐                  │
│    │    ↓ 精确匹配（metadata）     │                  │
│    │    返回全部信息 + 来源       │  ← 不调 LLM     │
│    └────────────────────────────┘                  │
│                                                    │
│    ┌── 自然语言 / 名称 / 原因 ──┐                  │
│    │    ↓ 复杂 NL? → LLM 提取   │                  │
│    │    ↓ Chroma 语义搜索       │                  │
│    │    返回匹配结果 + 来源行号  │                  │
│    └────────────────────────────┘                  │
└──────────────────────┬────────────────────────────┘
                       │
                       ▼
┌───────────────────────────────────────────────────┐
│              Chroma 向量库 (knowledge_base/)       │
│              BAAI/bge-small-zh-v1.5                │
│              133 条故障记录                         │
└───────────────────────────────────────────────────┘
          │
          ▼ (仅自然语言提取时调用)
┌───────────────────────────────────────────────────┐
│         DeepSeek API (deepseek-v4-flash)           │
│         仅用于：关键词提取（prompt 极小）           │
└───────────────────────────────────────────────────┘
```

### 文件类型 → Collection 映射

| 文件类型 | 默认 Collection | 用途 | 处理方式 |
|---------|----------------|------|---------|
| .pdf | `standards` | 标准文档查询 | PyMuPDF 提取文字 → 结构分析切块 → 入库 |
| .xlsx / .xls | `error_codes` | 故障代码查询 | openpyxl 按行解析 → 格式化 → 入库 |

### 查询策略详解

| 场景 | 匹配方式 | 是否调 LLM | 速度 |
|------|---------|-----------|------|
| 用户说 "d4-1" | 故障代码精确匹配（Chroma metadata 过滤） | ❌ 不调 | 毫秒级 |
| 用户说 "外部急停信号闭合" | Chroma 语义搜索 | ❌ 不调 | 毫秒级 |
| 用户说 "我这个逆变器报错了怎么回事" | DeepSeek 提取关键词 → Chroma 搜索 | ✅ 仅提取 | 较快 |
| 用户说 "硬件" | Chroma 语义搜索 | ❌ 不调 | 毫秒级 |

### 回复格式

每条结果格式（精确匹配）：
```
1. 【名称】急停告警
2. 【故障原因】外部急停信号闭合
3. 【故障代码】d4-1

4. 【地址】0x3004
5. 【位地址】bit0
6. 【属性】R
7. 【数据类型】bit
8. 【默认值】0
9. 【备注】一级  满足条件复位
10. 【说明】1-告警，0-正常
11. 【备注2】1

---
数据来源：PCS参数表 V1.6.2.xlsx → 遥信（DI） → 第51行（序号：1）
```

- 标签用 `【】` 中文括号代替 Markdown `**`（钉钉不渲染 Markdown 粗体）
- 内容行连续编号，空行/分隔线/数据来源行不占序号
- 数据来源行无括号、不编号
- 语义搜索多条结果时，每条独立编号，用 `────────── 结果 N ──────────` 分隔

### 钉钉 Stream 模式

> **传统 Webhook 模式** = 你开一个窗口等别人敲门，需要你家地址（公网 IP）
>
> **Stream 模式** = 你拉一根专线到钉钉总部，有消息直接顺线传过来

- 方向：**我们主动连钉钉**（WebSocket 长连接），不是钉钉连我们
- SDK：`dingtalk-stream` 库
- 关键注意：`ChatbotHandler.process()` 是 `async def`，但 `reply_markdown()` / `reply_text()` 是**同步方法**，不可 `await`
- 优势：不需要公网 IP、不需要防火墙白名单、不需要 SSL 证书、不需要域名
- 当前使用方式：搜索「恩特小助手」→ 进入单聊 → 直接发送问题（无需群聊 @）

## 当前组件

| 组件 | 选型 | 说明 |
|------|------|------|
| 数据源 | PCS参数表 V1.6.2.xlsx + 标准 PDF | 遥信（DI）sheet 133 条 + 国标/行标/国际标准 |
| 文档管理 | doc_mgr 子系统 | 统一上传→切块→入库，含 Web 管理页面（/admin） |
| 存储抽象层 | VectorStore ABC → ChromaStore | 后续切 Qdrant 只需改 storage.py 一处 |
| PDF 切块引擎 | PyMuPDF 结构分析 | 多信号融合（章节号 + 字体名 + 左边界），零新依赖 |
| Markdown 切块 | MarkdownChunker | 按标题层级（# ## ###）智能切块，中文标准章节号提取 |
| OCR 引擎 | MinerU VLM（大模型视觉识别） | 扫描 PDF → Markdown，替代传统 OCR 路线 |
| 向量数据库 | Chroma | 本地持久化，支持精确 + 语义搜索 |
| Embedding | BAAI/bge-small-zh-v1.5 | 国产中文嵌入，30MB，CPU 运行 |
| LLM | DeepSeek API (deepseek-v4-flash) | 关键词提取 + 聊天托底 |
| Web 框架 | FastAPI | Web 页面 + HTTP API 入口（/admin 挂载） |
| 钉钉 SDK | dingtalk-stream | Stream 模式，WebSocket 长连接，无需公网 IP |
| Agent 路由 | agent.py | 统一 Agent 循环：路由、聊天托底、记忆管理 |
| 标准查询 | standards_query.py | 标准文档检索工具（含标准编号快速通道 v1.2.3） |
| 文件接收 | file_handler.py | 钉钉文件/图片接收 → 自动下载保存到 data/uploads/ |
| 部署 | Docker + docker-compose | 可选容器化部署 |

## 运行方式

```bash
# 同步故障代码知识库（修改 Excel 后运行）
python scripts/sync_kb.py

# 同步标准文档知识库（添加标准 PDF 后运行）
python scripts/sync_standards.py

# 启动服务（Web + 钉钉机器人同时启动）
python scripts/main.py
# → Web: http://localhost:8000
# → 钉钉搜索「恩特小助手」单聊使用
# → 同一局域网：http://[本机IP]:8000

# 防火墙放行（局域网共享需要）
netsh advfirewall firewall add rule name="恩特小助手 8000" dir=in action=allow protocol=TCP localport=8000

# Docker 部署（可选）
docker-compose -f deploy/docker-compose.yml up -d
```

## 钉钉机器人部署流程

1. **钉钉开发者后台** → 创建企业内部应用 → 开启机器人能力 → 选 Stream Mode
2. **复制 ClientID + ClientSecret** → 填到 `scripts/local_config.py`
3. **版本管理与发布** → 创建版本 → 发布（管理员可设免审批，否则需审批）
4. **安装应用到公司组织** → 钉钉管理后台 → 应用管理 → 授权安装
5. **员工在钉钉搜索「恩特小助手」** → 进入单聊使用
6. 群聊机器人功能正在测试中，待稳定后加入开发群

## 设计原则

1. **不要过早工程化** — 先跑通，再优化
2. **Agent = LLM + 工具 + 记忆 + 循环** — 任何时候都回归这个模型思考
3. **渐进式升级** — 从最小可运行开始，逐步加能力
4. **可观测性优先** — 每个步骤应透明可追踪
5. **LLM 只用在刀刃上** — 能本地匹配就不调 API，省时间省钱

## 待办事项

- [x] 第一步 MVP：故障代码智能查询
- [x] 接入钉钉 Stream 模式机器人并上线
- [x] 替换为实际工程数据（PCS参数表 133 条）
- [x] 局域网共享
- [x] Git 版本管理初始化
- [x] 项目结构整理
- [x] 标准文档查询（第一阶段：文本 PDF 入库）
- [x] 扫描 PDF 标准 OCR 识别（MinerU 大模型视觉识别，v1.2.2）
- [x] 钉钉文件接收功能（v1.2.3）
- [ ] 第二、三步规划（参考调研报告）
- [ ] 技能扩展：经验查询、钉钉知识库对接

## 已知问题 / 注意事项

- Windows 控制台 GBK 编码可能无法输出 emoji，日志用纯文本符号
- 语义搜索短关键词（如 2 字）匹配效果可能不理想，后续可优化
- 服务跑在本地电脑，息屏/睡眠会断开钉钉连接
- `dingtalk_stream.ChatbotHandler.process()` 是 async 方法，但 SDK 内的 reply_* 方法是同步的，不要对它们用 `await`
- 当前使用方式为钉钉单聊（搜索机器人），群聊功能暂未开放
- `logs/` 目录运行时自动生成，已被 `.gitignore` 忽略不退版本
- `data/standards/mineru_output/` 是 MinerU OCR 中间产物（185MB+），已加入 `.gitignore`，不退版本管理。如需重新入库，运行 `sync_mineru.py`
