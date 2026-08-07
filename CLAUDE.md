# CLAUDE.md — 恩特能源 AI Agent 项目

## 🤖 与 AI 协作规则

1. **始终使用中文回答** — 无论用户输入什么语言，均以中文回复
2. **回答格式约定** — 每次回答开头用一个 emoji 表情，结尾加上「嘿嘿」
3. **检查不等于修改** — 当用户要求查问题/审代码时，执行检查并报告发现，但不要顺手改掉代码。用户可能只是想理解原理，不一定真有 bug
4. **保持质疑，主动补位** — 用户的想法不一定绝对正确，可以提出质疑和不同角度。同时主动补充用户可能不了解的原理、背景知识和代码常识，帮用户把认知盲区补上
5. **打标签前先更新 CHANGELOG 并同步版本号** — 当用户要提交版本/打 tag 时，先检查 CHANGELOG.md（唯一版本记录）是否已包含当前版本的完整变更；再同步 README.md / PROGRESS.md 的当前版本号与最近版本亮点。完整版本历史只在 CHANGELOG.md 维护，不要在多处复制
6. **主动提醒版本更新** — 每次完成实质性改动（新功能、修 bug、改配置等）后，主动问用户是否要更新版本号，并推荐升哪一位（major/minor/patch），同时附上一句话说明理由

## 项目概述

恩特能源（天津恩特能源科技有限公司，Tianjin Entar Energy Technology Co., Ltd.，品牌 ENTAR）AI Agent 项目。目标是搭建面向中小企业的 AI Agent 全生命周期管理平台。

### 当前进度：三步走计划 — 第一步稳定化 + 第二步经验知识库启动（v1.10.0）

**第一步：智能查询 + 标准文档检索 + 通用聊天 + 多技能（故障/标准/PCB 计算）——核心能力已完成，正在做真实环境验收与安全收尾**
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
- ✅ SQLite 用户、对话和权限存储（v1.2.4）
- ✅ Web 同步管理、强制重学和任务进度追踪（v1.2.5）
- ✅ SHA-256 内容指纹、安全版本替换和 MinerU 安全加固（v1.2.6）
- 🧪 固定审核人主动推送与审批口令已完成代码和离线测试，尚未真实发消息
- ✅ 278 项自动化回归测试（含 65 项 PCB 计算 + 文档处理/版本替换/上传审核 + 并发/路由 + 通讯录查询 + 反馈/Prompt/引用溯源 + AI 卡片流式）
- ✅ 局域网共享（防火墙放行端口 8000）
- ✅ 同事实测通过
- ⚠️ v1.4.1 尚未在正式知识库、真实 MinerU 和钉钉生产环境完成端到端回归

## 项目结构

```
D:\ENTAR_AGENT\
├── CLAUDE.md                                    # ← 项目级指南（本文件）
├── CHANGELOG.md                                 # 版本变更日志
├── README.md                                    # 项目说明
├── .gitignore                                   # Git 忽略规则
├── requirements.txt                             # Python 依赖清单
│
├── data/                                        # 📁 数据目录
│   ├── fault_codes/                             #    故障代码 Excel 数据
│   │   └── PCS参数表 V1.6.2.xlsx                #    实际工程 PCS 参数表（133 条故障）
│   ├── standards/                               #    标准文档 PDF 文件（含 MinerU 提取输出）
│   ├── pcb/                                     #    PCB 公式权威源（pcb-tools.cn 基准 + 查表）
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
│   ├── dingtalk_notifier.py                     # 🔔 钉钉机器人主动单聊（未发布）
│   ├── knowledge_review.py                      # ✅ 上传审核与同步触发（未发布）
│   ├── user_store.py                            # 💾 SQLite 用户/会话/权限存储（v1.2.4）
│   ├── web_page.py                              # 🌐 网页处理脚本
│   ├── prompts/                                 # 📄 外置提示词目录（v1.2.3）
│   │   └── system_prompt.txt                    #    系统提示词
│   ├── doc_mgr/                                 # 🆕 v1.2.1 文档管理子系统
│   │   ├── __init__.py, models.py, storage.py   # ⭐ 存储抽象层 + 文档版本替换
│   │   ├── identity.py                          #    稳定文档 ID + SHA-256 指纹
│   │   ├── engine.py, router.py, views.py       #    引擎 + API + 管理界面
│   │   ├── scheduler.py, sync_tracker.py        #    同步调度 + 状态追踪
│   │   ├── task_manager.py                      #    后台任务队列
│   │   ├── recovery.py                          # 🆕 崩溃恢复（清理遗留 staging/retired）
│   │   ├── chunkers/                            #    切块器（PyMuPDF 结构分析 + Markdown 标题层级）
│   │   │   ├── pymupdf_chunker.py               #    PyMuPDF 结构分析切块
│   │   │   ├── markdown_chunker.py              # 🆕 Markdown 标题层级切块（v1.2.2）
│   │   │   ├── unstructured_chunk.py            #    Unstructured 备用
│   │   │   └── fallback.py                      #    滑动窗口回退
│   │   └── extractors/                          #    文本提取（Excel + PyMuPDF）
│   ├── tools/                                   # 🆕 工具注册中心（v1.2.7）
│   │   ├── __init__.py                          #    @register 注册 + 分发
│   │   ├── search_knowledge_base.py             #    知识库检索工具
│   │   ├── search_standards.py                  #    标准检索工具
│   │   ├── calc_pcb_trace.py                    #    PCB 走线计算工具（IPC-2221）
│   │   └── search_experience_kb.py              # 🆕 经验知识库检索工具（v1.4.2）
│   └── skills/
│       ├── __init__.py
│       ├── agent.py                             # 🤖 Agent 循环路由（聊天托底）
│       ├── dingtalk_bot.py                      # 🤖 钉钉 Stream 模式机器人
│       ├── error_query.py                       # 🔧 故障查询（两级查询策略）
│       ├── standards_query.py                   # 📋 标准文档查询
│       ├── enhanced_search.py                   # 🆕 混合检索 + 重排（v1.3.0）
│       ├── pcb_calc.py                          # 🆕 PCB 计算技能 54 类（v1.5.4 以 pcb-tools.cn 为基准）
│       ├── experience_query.py                  # 🆕 经验知识库查询（v1.4.2）
│       └── memory.py                            # 💭 会话记忆管理
│
├── tests/                                       # 🧪 文档引擎/同步/安全/版本替换测试
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
└── .claude\                                     # Claude Code 配置
```

## 技术架构

```
浏览器 / 钉钉单聊
        │
        ▼
FastAPI (main.py) → Agent 循环（agent.py）
        ├─ 精确故障代码 / 标准编号 → 快速通道（不调 LLM）
        └─ 其他问题 → DeepSeek Function Calling
                       ├─ tools/ 工具注册中心
                       │     ├─ search_knowledge_base（故障知识库）
                       │     ├─ search_standards（标准知识库）
                       │     └─ calc_pcb_trace（PCB 走线计算）
                       ├─ 技能：pcb_calc（54 类 PCB 计算器）
                       ├─ 增强检索 enhanced_search
                       │     ├─ 向量 + BM25 双路召回 → RRF 融合
                       │     └─ bge-reranker 重排
                       └─ 无需检索时直接回答（聊天托底）

查询工具 → doc_mgr.storage → Chroma
                           ├─ error_codes
                           └─ standards
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
| 文档一致性 | SHA-256 + staging/active/retired | 内容变更检测、安全版本切换、失败回滚 |
| 存储抽象层 | VectorStore ABC → ChromaStore | 后续可新增 Qdrant 适配器并切换注册实现 |
| PDF 切块引擎 | PyMuPDF 结构分析 | 多信号融合（章节号 + 字体名 + 左边界），零新依赖 |
| Markdown 切块 | MarkdownChunker | 按标题层级（# ## ###）智能切块，中文标准章节号提取 |
| OCR 引擎 | MinerU VLM（大模型视觉识别） | 扫描 PDF → Markdown，替代传统 OCR 路线 |
| 向量数据库 | Chroma | 本地持久化，支持精确 + 语义搜索 |
| Embedding | BAAI/bge-small-zh-v1.5 | 国产中文嵌入，30MB，CPU 运行 |
| LLM | DeepSeek API (deepseek-v4-flash) | 关键词提取 + 聊天托底 |
| Web 框架 | FastAPI | Web 页面 + HTTP API 入口（/admin 挂载） |
| 钉钉 SDK | dingtalk-stream | Stream 模式，WebSocket 长连接，无需公网 IP |
| Agent 路由 | agent.py | 统一 Agent 循环：路由、聊天托底、记忆管理 |
| 工具注册 | tools/ | @register 装饰器注册，新增工具无需改 agent.py（v1.2.7） |
| 标准查询 | standards_query.py | 标准文档检索工具（含标准编号快速通道 v1.2.3） |
| 增强检索 | enhanced_search.py | 向量 + BM25 双路召回 RRF 融合 + bge-reranker 重排（v1.3.0） |
| PCB 计算 | pcb_calc.py + tools/calc_pcb_trace.py | 54 类 PCB 计算器，全本地秒回（v1.5.4 以 pcb-tools.cn 为基准） |
| 文件接收 | file_handler.py | 钉钉文件/图片接收 → 自动下载保存到 data/uploads/ |
| 上传审核 | knowledge_review.py + dingtalk_notifier.py | 固定审核人主动通知、一次性审批和后台同步 |
| 崩溃恢复 | doc_mgr/recovery.py | 启动时清理/恢复遗留 staging/retired 版本数据 |
| 自动化测试 | unittest | 278 项文档引擎、同步追踪、PCB 计算、安全和版本替换、并发路由、通讯录查询、反馈/Prompt/引用溯源、AI 卡片流式测试 |
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

> 📋 **唯一事实源：[TODO.md](TODO.md)** —— 全部待办以 TODO.md 为准，本文件不再逐条维护。
> 相关：进度看板 [PROGRESS.md](PROGRESS.md)（里程碑/完成度）· 版本计划 [CHANGELOG.md](CHANGELOG.md)（唯一版本记录）

当前待办分类（完整清单见 [TODO.md](TODO.md)）：
- 🔥 **P0**：真实环境验收、崩溃恢复、MinerU 部署、审核钉钉端到端验证
- ⚠️ **P1**：管理端安全收尾、Web 身份、部署安全
- 📌 **P2**：检索评测（RAGAS）、CI/可观测性
- 🎯 **第二步**：经验知识库（三步走核心目标）

## 已知问题 / 注意事项

- Windows 控制台 GBK 编码可能无法输出 emoji，日志用纯文本符号
- 语义搜索短关键词（如 2 字）匹配效果可能不理想，后续可优化
- 服务跑在本地电脑，息屏/睡眠会断开钉钉连接
- v1.2.6 的版本替换锁只保证单进程一致性，多 worker/多容器前需共享锁或活动版本指针
- 旧数据缺少 `doc_id` 时按文件名兼容匹配，同名文件可能产生歧义
- retired 旧版本若物理删除失败会继续占空间，需要后台清理任务
- 上传校验（路径/大小/MIME）、XSS 转义与 Docker 非 root 已补齐（v1.4.2）；Web 登录/SSO 可信身份识别仍待 v2.0 与多中心权限一起做
- `dingtalk_stream.ChatbotHandler.process()` 是 async 方法，但 SDK 内的 reply_* 方法是同步的，不要对它们用 `await`
- 当前使用方式为钉钉单聊（搜索机器人），群聊功能暂未开放
- 固定审核人主动推送尚未做真实钉钉端到端验证，需先配置 staff_id 并在副本知识库测试
- `logs/` 目录运行时自动生成，已被 `.gitignore` 忽略不退版本
- `data/standards/mineru_output/` 是 MinerU OCR 中间产物（185MB+），已加入 `.gitignore`，不退版本管理。如需重新入库，运行 `sync_mineru.py`
