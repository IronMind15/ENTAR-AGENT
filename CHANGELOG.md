# 恩特小助手 更新日志

> 📌 **本文档是项目唯一的版本记录（单一事实源）**——README.md、PROGRESS.md 的版本历史均指向本文件，发版时只在这里追加记录。
> 相关：待办清单见 [TODO.md](TODO.md)，进度看板见 [PROGRESS.md](PROGRESS.md)。

## v1.4.1（2026-08-04）

PCB 计算技能扩展与修复（新增 3 类计算器 + 修复 2 个崩溃 bug）：

### 新增

- **PCB 计算技能 9 类 → 12 类**（`skills/pcb_calc.py`）：
  - **IPC-2152 精确载流**：ΔT^0.5·A^0.65 工程近似，与 IPC-2221 对照输出（已知电流反推线宽 / 已知线宽算载流 / 校验三模式）
  - **差分对阻抗**：Zdiff = 2×Z0×(1−0.48e^(−0.96s/h))，支持 USB 90Ω / 以太网 100Ω 目标**反推线宽**
  - **安规距离**：IEC-60664-1 电气间隙 / 爬电距离，查表 + 线性插值，支持污染等级 / 材料组 / CTI 推断 / 海拔修正 / 加强绝缘

### 修复

- **差分对计算器整体崩溃**：`re.search(已编译正则, str, flags)` 在 Python 3.7+ 抛 `ValueError`，正推/反推全挂 → 按 pattern 类型分支调用
- **信号波长计算崩溃**：`_extract_length` 无匹配返回 `(None, "m")`，误判导致 `1000 × None` TypeError，「100MHz 波长」直接报错
- **「0.2152 毫米」误路由**：`\b2152\b` 防不住带空格的数值，误进 IPC-2152 → 加 `(?<![\d.])` 负向断言

### 完善

- 爬电距离查表扩展至 4000V（IEC-60664-1 表 4A），修复 2.5kV 低估（材料组 II：7.1mm → 17.5mm）
- 电气间隙 / 爬电距离超出查表范围时输出「（超出表范围，取上界）」警示
- 信号波长支持 kHz / Hz 低频；趋肤深度频率提取重构，消除潜在 None 警告
- 材料组 "III" 走更保守的 III 列；清理 5 个未使用正则与 εr 冗余分支

### 验证

- PCB 计算测试 34 → 44 项，全套件 84 项自动化测试通过
- 实测：差分反推（100Ω→462μm）、波长（100MHz→1463.9mm）、安规（2.5kV 爬电 17.5mm）数值符合工程预期

## v1.4.0（2026-08-03）

PCB 设计计算技能 + 钉钉即时反馈 + rerank 生效 + 检索稳定性修复：

### 新增

- **PCB 设计计算技能**（全新技能，`skills/pcb_calc.py`）：9 类计算器，纯本地公式秒回，参数正则提取不调 LLM
  - **走线**：IPC-2221 线宽 / 载流 / 压降，含「校验」模式（给定线宽能否过目标电流）
  - **阻抗**：IPC-2141 微带线 / 带状线特征阻抗，支持**已知目标阻抗反推线宽**
  - **过孔**：载流能力 / 电阻 / 所需并联过孔数（环形截面）
  - **趋肤深度**：δ ≈ 66/√f mm（高频电流等效截面）
  - **信号**：走线传播延迟（ps/inch）与波长 / 1/4 波长
  - **热设计**：结温 TJ = TA + θJA × P，可反推允许功耗
  - **通用电路**：LED 限流电阻 / 电阻分压 / RC 时间常数
- **PCB 计算 Agent 工具**（`tools/calc_pcb_trace.py`）：口语化复杂表达由 LLM 识别参数后调用，计算逻辑与技能共用同一套公式
- **钉钉「正在处理」即时反馈**：走 Agent/LLM 的慢操作先回「⏳ 收到，正在处理中…」，文件/图片回对应提示；故障精确、PCB 计算等秒回操作不提示，避免打扰
- **错误码体系**：处理异常时返回「❌ 处理出错了（错误码：1000/1001）」，不再静默失败

### 完善

- **rerank 重排实际生效**：bge-reranker 模型下载完成并启用（v1.3.0 仅框架就绪），检索排序质量进一步提升
- **embedding 离线加载**：修复模型联网检查更新失败导致检索全空的问题，改为本地缓存加载，检索稳定性恢复
- 依赖新增 jieba / rank_bm25（混合检索），本地模型目录 models/ 不入版本管理

### 验证

- 74 项自动化测试通过（新增 PCB 计算 24 项 + 钉钉体验 7 项）
- 真实知识库实测：9 类 PCB 计算器全部路由正确；rerank 对故障检索排序有效

## v1.3.0（2026-08-03）

检索质量升级 — 混合检索 + 重排框架（对应用户最痛的「短关键词检索差」）：

### 新增

- **混合检索**（`skills/enhanced_search.py`）：语义向量检索 + BM25 关键词检索双路召回，RRF（Reciprocal Rank Fusion）融合排名
  - BM25 用 jieba 中文分词 + rank_bm25 稀疏检索，精确匹配字面词
  - 故障库与标准库的语义搜索路径统一接入，短关键词（如「过压」「硬件」）召回明显提升
  - BM25 索引带缓存（按 collection + 过滤条件），增量重建，重复查询零开销
- **重排框架**（同模块，`use_rerank=True`）：对融合后 Top-15 候选用 bge-reranker 精排，提升排序质量
  - 模型优先加载本地 `models/bge-reranker-base`（权重齐全才用），否则回退远程名
  - ⏳ 模型未下载完成（国内网速限制），代码就绪、模型到位自动启用；当前实测走混合检索路径

### 优化

- 精确匹配快速通道（故障代码 / 标准编号）不受影响，仍是不调 LLM 的毫秒级响应
- 降级链完整：jieba / rank_bm25 缺失、BM25 构建失败、rerank 模型不可用 → 自动回退纯向量检索，绝不崩溃

### 依赖

- 新增 `jieba`、`rank_bm25`（轻量纯 Python 包）
- 本地模型目录 `models/` 已加入 .gitignore（可重新下载，不退版本）

### 验证

- 33 项自动化测试通过
- 真实知识库实测：短词「过压」「硬件」混合检索全部命中相关故障，纯向量存在噪声

## v1.2.9（2026-08-03）

代码审查后的一次集中修复，重点解决多中心部门隔离失效和 /admin 安全缺口：

### 修复

- **多中心部门不再被清零**：`upsert_file` 改为「空值不覆盖」，同步路径（后台手动、批量、调度）不再把上传时指定的中心覆盖为 public；审核申请创建时把建议部门真正落库，审批通过后按真实部门入库（v1.2.8 多中心主流程缺陷）
- **/admin 读接口补齐鉴权**：collections / docs / search / upload-status 四个只读端点补密码校验，前端同步带 `getPw()`；上传表单补传 password（此前配置密码后前端上传实际不可用）
- **统计页 XSS 修复**：`/admin/stats` 用户可控字段（昵称、职位、部门、user_id）统一 HTML 转义
- **任意文件删除拦截**：sync-delete / sync-trigger 增加 data/ 目录路径白名单，防止删除或入库服务器任意路径文件
- **DeepSeek 瞬断自动重试**：对连接错误（10061）、超时、5xx、429 做指数退避重试（1s→2s），ISP 偶发波动不再导致查询全量失效
- **content 为 None 崩溃修复**：LLM 返回空 content（thinking 模式）时不再触发 `.strip()` 崩溃，Agent 与关键词提取均兼容

### 验证

- 33 项自动化测试通过（新增 4 项 department 行为回归测试）
- 全量 Python 语法检查通过

## v1.2.8（2026-07-22）

多中心部门知识库基础架构 + 用户中心管理：

### 新增

- **五中心组织架构配置**（`scripts/center_config.py`）：定义 PMO、研发、制造、商业、运营 + 公共区六类中心，提供 `resolve_center()`、`get_center_name()`、`list_centers()` 等工具方法
- **用户多中心归属**：`user_store.users` 表新增 `centers` 字段（JSON 数组），支持用户同时归属多个中心，提供 `get_user_centers()`、`set_user_centers()` 接口
- **入库部门标签**：`engine.process_file()` 新增 `department` 参数，所有新入库 chunk 自动写入 `department` metadata，按中心隔离
- **审核流程嵌部门**：钉钉上传审核通知展示上传人全部归属中心，审核人可在批准时指定或覆盖部门归属
- **多中心查询过滤**：`standards_query.search_kb()` 接受 `centers` 列表参数，自动构建 `$in` 过滤（用户中心 + 公共区），结果不足时回退到无条件搜索（兼容旧数据）
- **Web 用户管理页面**：管理后台新增「用户管理」Tab，支持勾选设置用户归属中心

### 安全

- `suggested_department` 沿审核流程逐级传递，审核人确认后写入入库管道，杜绝部门归属绕过审核
- 旧用户无 `centers` 字段时自动回退到 `center` 单字段，再回退到 `public`，不产生权限黑洞
- 旧数据无 `department` 标签时查询自动走无条件回退，存量数据不受影响

### 验证

- 29 项回归测试全部通过
- 多中心设置、读取、回退、格式化展示均通过独立验证

## v1.2.7（2026-07-22）

工具注册中心重构与上传审核工作流：

### 重构

- **工具注册中心**（`scripts/tools/`）：新增基于 `@register` 装饰器的工具注册表，替代 `agent.py` 中硬编码的 `TOOLS` 列表和 `if-elif` 工具路由。新增工具只需建文件 + 一行装饰器，无需改 `agent.py`
- **`agent.py` 精简 96 行**：`TOOLS` 定义从 36 行减为 1 行（`get_tool_definitions()`），`_execute_tool` 从 74 行减为 13 行（委托注册中心路由）
- **工具与定义同文件**：每个工具的 DeepSeek API schema 和执行逻辑放在同一模块，不再跨文件分散

### 新增

- **上传审核主动推送**：钉钉员工上传 PDF、Excel 或 Markdown 后，机器人主动单聊固定审核人，消息包含申请编号、上传人、文件名、大小和建议目标库
- **钉钉审批口令**：固定审核人可回复 `同意同步 申请编号` 或 `拒绝同步 申请编号 原因`；批准后复用现有后台任务同步知识库
- **审核人身份查询**：向机器人发送 `查看我的审核ID` 可取得配置主动推送所需的钉钉员工 ID
- **可扩展审核人解析器**：上传流程与审核人选择解耦，后续可从固定审核人切换为按上传者部门寻找主管

### 安全

- 仅申请中指定且当前配置有效的审核人可操作，申请采用一次性状态流转，重复回复不会重复入库
- 批准前校验源文件仍位于 `data/uploads/` 且扩展名受支持，主动消息不暴露服务器文件路径
- 钉钉主动消息在后台线程发送；网络失败不影响文件保存，文件继续留在待处理区

### 验证

- 新增主动消息参数、token 缓存、审核鉴权、重复审批、拒绝流程和路径越界测试
- 尚未向真实钉钉账号发送测试消息，也未对正式知识库执行批准入库

## v1.2.6（2026-07-21）

文档同步一致性与 MinerU 安全加固，避免重复入库、旧版本残留和不完整 OCR 结果进入知识库：

### 修复

- **同步状态可靠记录**：修复部分处理分支提前返回导致同步状态无法落库的问题；处理异常现在会统一记录为 `error`
- **内容变更准确识别**：使用文件 SHA-256 替代修改时间作为内容指纹，避免时间戳未变化或被复制工具改写时误判
- **Collection 路由一致**：统一在入口解析目标 Collection，PDF、Excel、Markdown 均尊重调用方指定的存储目标
- **Excel 行 ID 冲突**：行 ID 加入稳定文档身份，避免不同目录或同名工作簿的行号相互覆盖
- **MinerU 缓存失效**：缓存与源文件内容哈希绑定；强制重学会绕过缓存，不再复用过期 Markdown
- **大 PDF 分段完整性**：所有分段成功后才按顺序合并，任一段缺失即停止入库，避免半份文档进入知识库
- **查询版本漂移**：故障代码与标准查询统一复用文档管理存储实例，只返回当前可见版本

### 新增

- **稳定文档身份**：新增路径规范化文档 ID 与流式 SHA-256 工具，为版本替换和同步追踪提供统一标识
- **文档版本替换**：新版本先以 staging 状态分批写入并校验，再切换为 active；失败时清理新数据并保留旧版本可查询
- **旧版本隔离**：查询层自动隐藏 staging/retired 数据，物理删除失败时也不会把旧内容返回给用户
- **MinerU ZIP 安全校验**：拦截路径穿越、绝对路径、Windows 盘符、符号链接、异常压缩比及超限文件数量/体积
- **自动化回归测试**：新增文档引擎、同步追踪、MinerU 安全和版本替换测试，共 20 项

### 优化

- **大 PDF 临时文件隔离**：每段 MinerU 输出使用独立临时目录，合并文件采用原子替换，任务结束自动清理
- **写入并发保护**：替换、删除和查询共享可见性锁，降低同进程并发时新旧版本交叉暴露风险
- **向量存储复用**：查询模块不再各自创建 Chroma 客户端和 Embedding 实例，减少重复模型加载与状态分叉

### 验证

- `python -B -m unittest discover -s tests -q`：20 项测试通过
- 40 个脚本与测试文件通过 Python AST 语法检查
- 使用内存 Chroma 集成验证版本替换后，精确查询和语义查询均只返回新版本

## v1.2.5（2026-07-16）

用户文件上传 + Web 同步管理完善，MinerU 稳定性提升：

### 新增

- **Web 同步管理流程完善**：钉钉上传 → Web 后台「同步管理」Tab → 手动触发入库，支持进度条实时追踪
- **后台任务管理器**（`task_manager.py`）：基于 ThreadPoolExecutor 的异步任务队列，同步不阻塞 HTTP
- **同步进度追踪器**（`sync_tracker.py`）：SQLite 记录文件同步状态（pending/synced/error），含上传者信息
- **强制重学按钮**：跳过 Chroma 查重，重新走 MinerU 处理并入库
- **MinerU Token 统一管理**：`local_config.py` 新增 `MINERU_TOKEN` 配置项，优先级高于 `~/.mineru/config.yaml`

### 优化

- **目录结构重构**：源文件在哪个目录，`mineru_output/` 就建在哪个目录下
  - `data/standards/` → `standards/mineru_output/`（Web 上传文件的结果）
  - `data/uploads/` → `uploads/mineru_output/`（钉钉上传文件的结果）
  - `data/fault_codes/` → `fault_codes/mineru_output/`（预留）
- **OSS 上传自动重试**：遇到网络超时自动重试 3 次（5s → 10s → 报错），偶发抖动自动恢复
- **同步去重优化**：不再在同步前做 Chroma 预检查，每次都走 MinerU 下载最新 ZIP，入库时通过 chunk_id 自动去重
- **清理旧格式**：删除了 14 个旧格式目录 + 散落 ZIP 文件，去除 8 套标准的重复数据
- **修复 `dingtalk_bot.py` 变量 bug**：`_sync_user_info_async` 中 `sender` 未定义问题

### 技术栈

- 异步任务：`ThreadPoolExecutor`（2 个 worker），线程本地存储 + 锁保护
- 同步状态：SQLite `sync_status` 表
- OSS 上传：`requests.put()` + 自动重试，超时 180s
- 目录策略：`_get_mineru_output_dir()` 动态路径函数

## v1.2.4（2026-07-15）

用户存储结构升级 — SQLite 统一存储 + 权限系统 + 钉钉信息同步：

### 新增

- **SQLite 用户信息存储模块**（`scripts/user_store.py`）：统一存储用户信息、对话记忆、权限管理，替代原有 JSON 文件方案
  - 三张表：`users`（用户信息）、`conversations`（对话记录）、`permissions`（权限配置）
  - WAL 模式 + 线程独立连接，读写不互斥，并发安全
  - `get_store()` 单例模式，全局共享一个连接池
- **数据迁移工具**（`scripts/migrate_json_to_sqlite.py`）：一键将 `chat_memory.json` 数据迁移到 SQLite，支持 `--dry-run` 预览
- **钉钉用户信息后台同步**（`dingtalk_bot.py`）：用户首次发言或超过 24h 后自动同步钉钉用户信息（姓名、职位、部门、是否为管理者）
- **统计看板页面**（`/admin/stats`）：累计用户、今日活跃、每日消息趋势、对话量 Top 10、所有用户列表（含部门/职位/身份）
- **Web 管理页权限保护**：设置 `ADMIN_PASSWORD` 后，管理页面、上传、删除操作需要密码验证
- **权限系统**：基于 `permissions` 表的细粒度权限控制，支持 upload/delete/manage 三种操作，主管默认有上传权限
- **配置项**（`config.py`）：`MEMORY_BACKEND`（sqlite/json 后端切换）、`MAX_CONTEXT_ROUNDS`（对话轮数）、`ADMIN_PASSWORD`（管理密码）

### 重构

- **记忆模块代理层**（`memory.py`）：从直接读写 JSON 重构为统一代理层，支持 sqlite/json 双后端，默认 sqlite。JSON 路径作为兼容回退
  - 接口不变（`add()` / `get_context()` / `format_context()`），外部代码零改动
  - SQLite 操作异常时自动回退 JSON，不影响服务可用性
- **router.py 权限拦截**：upload/delete 端点增加统一权限检查，支持 password 验证和 user_id 权限判定双模式

### 技术栈

- 存储：SQLite（内置，零额外依赖）
- 钉钉 API：旧版 `oapi.dingtalk.com` 接口（`topapi/v2/user/get`），通过 appkey + appsecret 获取 token
- 线程模型：`threading.local()` 每线程独立连接 + `RLock` 写互斥

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
