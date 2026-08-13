# CLAUDE.md — 恩特能源 AI Agent 项目

## 🤖 与 AI 协作规则

1. **始终使用中文回答** — 无论用户输入什么语言，均以中文回复
2. **回答格式约定** — 每次回答开头用一个 emoji 表情，结尾加上「嘿嘿」
3. **检查不等于修改** — 当用户要求查问题/审代码时，执行检查并报告发现，但不要顺手改掉代码。用户可能只是想理解原理，不一定真有 bug
4. **保持质疑，主动补位** — 用户的想法不一定绝对正确，可以提出质疑和不同角度。同时主动补充用户可能不了解的原理、背景知识和代码常识，帮用户把认知盲区补上
5. **打标签前先更新 CHANGELOG 并同步版本号** — 当用户要提交版本/打 tag 时，先检查 CHANGELOG.md（唯一版本记录）是否已包含当前版本的完整变更；再同步 README.md / PROGRESS.md 的当前版本号与最近版本亮点。完整版本历史只在 CHANGELOG.md 维护，不要在多处复制
6. **版本号由用户拍板，不自动写** — 完成实质性改动（新功能、修 bug、改配置等）后，只汇报改动 + **建议**升哪一位（major/minor/patch）+ 一句话理由，**等用户确认后才把版本号写进 CHANGELOG 并同步文档**；绝不自己定版本号写入文档（2026-08-11 用户明确要求）
7. **发版固定动作：commit 和 tag 绑定** — 用户确认版本号 → 写 CHANGELOG/README/PROGRESS → `git commit` + `git tag vX.Y.Z` **同一次操作同步完成**（tag 指向本次提交），不再单独记，防止漏打（2026-08-11 曾漏打 v1.11.0 tag）
8. **修 bug 先找根因，不许先改；skill 主动用，不等提醒**（2026-08-11）— 修 bug 调用 `awesome-bug-fix`（先建可复现 pass/fail 循环 → 定位根因 → 再改，禁止症状补丁）；改完代码补回归测试用 `awesome-test-writing`（tripwire 理念：每种可能的回归都有一条测试变红，新功能改动必须证明旧功能没被破坏）；发版前/复杂改动用 `awesome-code-review`（读被改行历史 + 切斯特顿栅栏：拆旧逻辑前先确认它当初挡着什么）；「为什么总是这样」类过程问题用 `awesome-root-cause`（5-Whys/PDCA）；老板/领导提模糊新功能用 `ent-feature-oracle`（能力映射 → 分层落地方案 → 天马行空发散，设计不实现，版本号只建议）。这些 skill 在项目 `.claude/skills/`，遇到对应场景主动调用，不等用户点名

## 项目概述

恩特能源（天津恩特能源科技有限公司，Tianjin Entar Energy Technology Co., Ltd.，品牌 ENTAR）AI Agent 项目。目标是搭建面向中小企业的 AI Agent 全生命周期管理平台。

### 当前进度：三步走计划 — 第一步稳定化 + 第二步经验知识库启动（v1.12.0）

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
- ✅ 上传文件直接学习（v1.10.2）：上传后回复「帮我学习」直接入库（取消主管审核）；目录存 `data/uploads/{主部门}/{员工}/{日期}/`；「我的文件」查看、「删除学习/重新学习」管理、`ENTARBOSS` 管理员模式可任意删改；审核代码保留注释可恢复
- ✅ PDF 检测路由（v1.10.3）：文字层检测（纯文字版/扫描版/混合版），纯文字版 PDF 走本地 PyMuPDF 免费高保真提取（省 MinerU 每日 1000 页额度），扫描/混合版走 MinerU 保质量；`PDF_ROUTING` 配置开关（auto 默认 / mineru 旧行为）一键切回；`compare_pdf_parsers.py` 双路径对比脚本（--no-mineru 干跑省额度）
- ✅ 每日项目看板（v1.11.0～v1.11.9）：发钉钉文档动态登记数据源 → 二次确认创建订阅 → 每天定时实时拉取。v1.11.6 补失败告警、恢复/删除、权限预检；v1.11.7 将完整非敏感字段分批交给 DeepSeek map/reduce，并以记录引用和字段原值校验每条结论、保留源文件链接；v1.11.8 改为今日变化优先、真实文件标题、主动消息表格转稳定列表、长报告按完整章节分页，并识别/抑制重复订阅推送；v1.11.9 修外部审查 4 条 Critical（确认词按用户隔离、MinerU 入库失败回退本地、跳过缓存目录、删除学习覆盖全部版本）。看板数据不入 Chroma。
- ✅ 钉钉文档类型自动识别（v1.11.1）：入口统一允许所有类型——发任意钉钉文档链接（AI表格 notable / 在线表格 workbook / 普通文档 doc）自动探测类型后按类型读取；doc 走 `GET /v1.0/doc/suites/documents/{node}/blocks`（需 **Storage.File.Read** 权限），blocks 逐块转保真 Markdown（段落→正文、表格→Markdown 表格、单元格 `\n`→`<br>`）；「帮我学习」doc 按全文 Markdown 入库；doc 也可做看板数据源（格式不统一，采集全文由 LLM 提炼）
- ✅ 实测修复包（v1.11.2）：一次发多个文档链接不再丢（`urls[:5]` 放宽）；卡片/富文本消息也能识别「做每日看板」意图（`_handle_doc_link_with_kanban` 合并文档摘要 + 看板创建确认，接入文本/富文本/卡片三处路由）；「帮我学习」说明加强——入库进哪个库（企业知识库·标准文档库）、怎么检索（自然语言命中示例）、看板不受影响（实时拉取不进库），入库成功提示补块数/记录数/「我的文件」入口
- ✅ 上传文件自动推荐入库（v1.11.0）：保存后回执主动推荐「要不要入库？回复『入库/确认』」→ 按路径入库（learn_file_path）；图片不支持不推荐
- ✅ 多知识库注册表（v1.11.5）：SQLite 表 `knowledge_bases`（key/name/description/collection/department/enabled）注册管理，种子故障/标准/经验三库；钉钉自然语言创建（「创建知识库，名字叫产品手册，用来放产品说明书，研发部」）；「把这个文档学到XX」学习入库指定库（target_collection/department 从 KB 继承）；`department` 字段为分部门开权限预留（`get_visible_knowledge_bases` 按 centers 过滤，现全 public 不拦截）
- ✅ 通用知识库查询工具（v1.11.5）：`kb_search(query, knowledge_base="")` 一个工具替代旧 3 个——指定库按 collection 分发（故障码精确/标准编号精确/经验语义/自定义 enhanced_query + department where 过滤），留空自动全可见库合并搜索，每条带 kb_name + source_label；旧 search_standards/search_experience_kb 文件保留不再注册（10→9 工具）
- ✅ 工具操作治理（v1.11.8）：工具注册支持 `policy`；查询/计算/识别等只读操作可直接执行，创建、删除、修改、启停、主动外发等写操作先冻结工具名和参数并要求用户再次确认；确认后以真实 handler 返回值决定成功/失败，禁止 LLM 把”准备执行”说成”已经执行”。覆盖创建知识库、主动推看板、总结后外发、上传文件删除/重学，以及看板订阅管理。
- ✅ 工具板块化重构（v1.12.0）：注册中心成为工具唯一事实源——LLM prompt 工具段（`render_tool_prompt`）、流式显示映射（`get_tool_display_map`）、能力清单（`capability_manifest.py --write` → docs/能力清单.md）全部由注册中心生成，不再手写；8 个工具改名带板块前缀（`search_knowledge_base→kb_search` 等，旧名读侧幂等迁移兜底 DB 遗留）；`system_prompt.txt` 工具段抽离只留角色/规则；10 个新工具名无旧名残留（tripwire 测试守卫）。
- ✅ 判定层治本框架（v1.12.0）：`scripts/routing.py` 三态判定（确定性直行/歧义交还用户/放行）——领域互斥让位（bot 删除学习正则接管前命中 kanban → 让位给看板技能，「删除看板订阅」不再被误抢）+ 操作歧义澄清（跨领域歧义句反问「回 1/2 或具体项」）+ 查询盲区不补枚举（CQC 3310 等落 LLM 走 `kb_search` 工具兜底）。三套确认 pending 结构统一优化已记 TODO（M3 后续）。
- ✅ SQLite 用户、对话和权限存储（v1.2.4）
- ✅ Web 同步管理、强制重学和任务进度追踪（v1.2.5）
- ✅ SHA-256 内容指纹、安全版本替换和 MinerU 安全加固（v1.2.6）
- ⏸️ 上传审核流程（v1.10.2 已停用）：固定审核人主动推送与审批口令代码保留注释，未来恢复部门划分与审核时取消 `queue_review_for_upload` 注释即可
- ✅ 813 项自动化回归测试（含 65 项 PCB 计算 + 文档处理/版本替换/并发/路由/通讯录/反馈/Prompt/引用溯源/识图/PDF 路由 + 看板全量证据链、字段变化检测、自然确认与真实删除、重复订阅识别和调度抑制、真实标题、表格安全降级、语义分页 + 通用工具二次确认 + v1.11.9 审查修复 5 项回归 + v1.12.0 注册中心 tripwire/能力清单完整性/判定层领域让位与查询链路）
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
│   ├── dingtalk_notifier.py                     # 🔔 钉钉机器人主动单聊/看板批量推送
│   ├── dingtalk_doc_client.py                   # 📑 钉钉三类文档读取 + dws 真实文件元信息
│   ├── dashboard_scheduler.py                   # ⏰ 看板定时调度、失败告警、重复推送抑制
│   ├── knowledge_review.py                      # ✅ 上传学习/删除/重学（旧审核代码保留）
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
│   ├── kb_registry.py                           # 🆕 知识库注册表（v1.11.5：SQLite 表 + 部门可见性）
│   ├── routing.py                               # 🆕 判定层（v1.12.0）：detect_domains 领域互斥 + ask_clarification 歧义澄清
│   ├── capability_manifest.py                   # 🆕 能力清单生成器（v1.12.0）：注册中心/技能/登记表 → docs/能力清单.md
│   ├── dashboard/                               # 📊 动态看板核心（采集/解析/证据链/订阅/推送）
│   │   ├── collector.py, parser.py              #    全动态数据采集 + 完整非敏感字段解析
│   │   ├── alerts.py, llm_pipeline.py           #    字段级变化 + DeepSeek map/reduce/证据校验/分页
│   │   ├── subscription_store.py                #    订阅持久化、指纹与重复识别
│   │   └── service.py, assembler.py             #    服务编排 + 钉钉 Markdown 安全渲染
│   ├── tools/                                   # 🆕 工具注册中心（v1.2.7，v1.12.0 板块化唯一事实源）
│   │   ├── __init__.py                          #    @register(definition, policy, sector, user_desc, display) 注册/分发 + 写操作二次确认 + render_tool_prompt/get_tool_display_map/get_tool_metadata
│   │   ├── kb_search.py             # 📚 通用知识库查询（v1.11.5：指定库分发/全库合并）
│   │   ├── kb_create.py             # 📚 钉钉自然语言创建知识库（二次确认）
│   │   ├── kb_file_manage.py        # 📚 上传文件删除/重学（二次确认）
│   │   ├── calc_pcb_trace.py        # 🧮 PCB 走线计算工具（IPC-2221）
│   │   ├── calc_copper_busbar.py    # 🧮 铜排/母线载流计算（v1.6.0）
│   │   ├── dash_query.py            # 📊 看板实时查询（保留来源链接）
│   │   ├── dash_push.py             # 📊 看板主动推送（二次确认）
│   │   ├── contact_find.py          # 👥 钉钉通讯录员工查询（v1.7.0）
│   │   ├── image_describe.py        # 🖼️ 图片识别（qwen3.7-flash 视觉，v1.10.0）
│   │   ├── doc_summarize.py         # 📄 文档总结（条件外发确认）
│   │   ├── search_standards.py      # ⚠️ 已停用注册（v1.11.5 由 kb_search 替代，文件保留）
│   │   └── search_experience_kb.py  # ⚠️ 已停用注册（v1.11.5 由 kb_search 替代，文件保留）
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
│   ├── 能力清单.md                               # 🆕 能力快照（v1.12.0：capability_manifest.py --write 自动生成）
│   ├── 20260813-意图识别验证报告.md               # 🆕 LLM 意图识别验证（v1.12.0 判定层决策依据）
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
FastAPI (main.py) → 判定层 routing.py（领域互斥/歧义澄清）→ Agent 循环（agent.py）
        ├─ 精确故障代码 / 标准编号 → 快速通道（不调 LLM）
        ├─ bot 秒回正则命令（_BOT_COMMANDS 登记表）
        └─ 其他问题 → DeepSeek Function Calling
                       ├─ tools/ 工具注册中心（板块化，v1.12.0）
                       │     ├─ kb_search（故障/标准/经验统一查询）
                       │     ├─ calc_pcb_trace / calc_copper_busbar（计算）
                       │     ├─ dash_query / contact_find / image_describe
                       │     └─ kb_create / dash_push / doc_summarize（写操作二次确认）
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
| PDF 检测路由 | classify_pdf_type + validate_local_text（v1.10.3） | 文字层覆盖率检测：纯文字版走本地 PyMuPDF（免费高保真，省 MinerU 每日 1000 页额度）、扫描/混合版走 MinerU 保质量；`PDF_ROUTING` 开关（auto 默认/mineru 旧行为）；`compare_pdf_parsers.py` 双路径对比脚本 |
| 每日项目看板 | dashboard 子系统 + dashboard_scheduler（v1.11.0～v1.11.9） | 全动态数据源；订阅持久化 owner 双身份；完整字段分批 DeepSeek map/reduce；今日变化优先；每条结论校验记录引用与字段原值；来源链接保留；主动消息表格转列表、长报告语义分页；失败告警；完全重复订阅不新建，同用户/同时间/同接收人且来源被完整覆盖的订阅同批只推覆盖最全的一条；v1.11.9 确认词按用户隔离防跨用户误拦截 |
| 钉钉文档类型识别 | dingtalk_doc_client（v1.11.1～v1.11.8）+ bot | `detect_kind` 三类型自动探测（notable→workbook→doc，403 权限错传播）；普通文档 blocks 转保真 Markdown；`read_document` 统一入口；v1.11.8 通过 dws 元信息读取文件级真实标题，优先于工作表名/首段并回填候选，失败安全降级 |
| 向量数据库 | Chroma | 本地持久化，支持精确 + 语义搜索 |
| Embedding | BAAI/bge-small-zh-v1.5 | 国产中文嵌入，30MB，CPU 运行 |
| LLM | DeepSeek API (deepseek-v4-flash) | 关键词提取 + 聊天托底 |
| Web 框架 | FastAPI | Web 页面 + HTTP API 入口（/admin 挂载） |
| 钉钉 SDK | dingtalk-stream | Stream 模式，WebSocket 长连接，无需公网 IP |
| Agent 路由 | agent.py | 统一 Agent 循环：路由、聊天托底、记忆管理 |
| 工具注册与操作治理 | tools/ | `@register(DEFINITION, policy, sector, user_desc, display)` 装饰器注册（v1.12.0：name 只从 definition 读，旧签名 TypeError/缺 name ValueError 快速失败），新增工具无需改 agent.py；v1.11.5 查询工具收敛为通用 `kb_search`；v1.11.8 注册策略区分只读与写操作，写操作生成 10 分钟待确认状态，确认后执行冻结参数并按真实工具结果回报；v1.12.0 注册中心成为唯一事实源（prompt 工具段/流式显示/能力清单自动生成），10 工具带板块前缀（kb/calc/dash/contact/image/doc） |
| 知识库注册表 | kb_registry.py（v1.11.5） | SQLite 表 `knowledge_bases`（key/name/description/collection/department/enabled）注册管理，种子三库（故障/标准/经验）；钉钉自然语言创建（tools/kb_create.py）；「把这个文档学到XX」学习入库指定库；`department` 字段 + `get_visible_knowledge_bases(centers)` 分部门开权限预留（现全 public 不拦截） |
| 判定层治本框架 | routing.py（v1.12.0） | `detect_domains` 领域互斥探测（fault/standard/pcb/kanban/file_cmd，惰性 import 防循环依赖）——bot 文件删除/重学正则接管前命中 kanban → 让位给看板技能；`ask_clarification` 操作歧义澄清（跨领域歧义句反问交还用户，按 user_id 隔离）；查询盲区（CQC 3310 等）不补枚举，落 agent 走 `kb_search`/`dash_query` 工具兜底 |
| 能力清单 | capability_manifest.py（v1.12.0） | 从注册中心/技能层/bot 命令登记表/看板意图登记表聚合生成 docs/能力清单.md（5 section），`python scripts/capability_manifest.py --write` 一键再生；bot 命令 `_BOT_COMMANDS`（12 项含 removed）+ 看板意图 `_INTENT_DEFS`（15 项）为数据源，regex 引用 `.pattern` 防触发词漂移 |
| 通用知识库查询 | tools/kb_search.py（v1.11.5） | `kb_search(query, knowledge_base="")` 指定库按 collection 分发（故障码精确/标准编号精确/经验语义/自定义 enhanced_query + department where 过滤），留空自动全可见库合并搜索，每条带 kb_name + source_label；旧 search_standards / search_experience_kb 文件保留不再注册 |
| 标准查询 | standards_query.py | 标准文档检索工具（含标准编号快速通道 v1.2.3） |
| 增强检索 | enhanced_search.py | 向量 + BM25 双路召回 RRF 融合 + bge-reranker 重排（v1.3.0） |
| PCB 计算 | pcb_calc.py + tools/calc_pcb_trace.py | 54 类 PCB 计算器，全本地秒回（v1.5.4 以 pcb-tools.cn 为基准） |
| 文件接收 | file_handler.py | 钉钉文件/图片接收 → 自动下载保存到 data/uploads/ |
| 识图能力 | image_describe.py + qwen3.7-flash | 钉钉发图自动识别描述（视觉外挂，v1.10.0）；tools/ 注册 + Claude Code vision skill；magic bytes + 路径白名单 + 5xx 重试 |
| 上传直接学习 | knowledge_review.py + dingtalk_notifier.py + file_handler.py | ⏸️ 审核已停用（v1.10.2）：上传→回「帮我学习」直接入库；「我的文件」查看；v1.11.8 删除/重新学习改为二次确认；`ENTARBOSS` 管理员模式保留；旧审核代码可恢复 |
| 崩溃恢复 | doc_mgr/recovery.py | 启动时清理/恢复遗留 staging/retired 版本数据 |
| 自动化测试 | unittest | 813 项通过；覆盖文档引擎、同步追踪、PCB、安全/版本替换、并发路由、通讯录、反馈/Prompt/引用、识图、PDF 路由、看板完整证据链/变化优先/真实标题/安全渲染/语义分页/重复调度、通用工具二次确认、v1.11.9 审查修复 5 项回归，以及 v1.12.0 注册中心 fail-fast/tripwire 无旧名残留/能力清单完整性/判定层领域让位与查询链路 |
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
- 上传审核流程已停用（v1.10.2）：上传文件改「回复『帮我学习』」直接入库，目录存 `data/uploads/{主部门}/{员工}/{日期}/`；审核代码（`queue_review_for_upload` 调用）保留注释，未来恢复时取消注释即可；管理员口令 `ADMIN_MASTER_CODE`（默认 `ENTARBOSS`，local_config 可覆盖），管理员会话为内存态，重启失效
- `logs/` 目录运行时自动生成，已被 `.gitignore` 忽略不退版本
- `data/standards/mineru_output/` 是 MinerU OCR 中间产物（185MB+），已加入 `.gitignore`，不退版本管理。如需重新入库，运行 `sync_mineru.py`
