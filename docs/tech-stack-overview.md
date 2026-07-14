# 🧰 恩特小助手技术栈全景

> 从零到上线，用到的和未来可能用到的技术全记录

---

## 目录

- [已掌握技术（当前项目在用）](#一已掌握技术当前项目在用)
- [未来可学技术（规划/调研中）](#二未来可学技术规划调研中)
- [技术栈演进路线图](#三技术栈演进路线图)

---

## 一、已掌握技术（当前项目在用）

### 1. Python — 主力编程语言

**说明：** 项目的绝对主力语言，所有后端代码都用 Python 写。Python 的优势是生态丰富（AI/ML 库最多）、上手快、社区庞大。

**项目案例：** 所有 `.py` 文件——`main.py`（服务入口）、`agent.py`（RAG Agent）、`sync_kb.py`（数据同步）、`memory.py`（记忆模块）、`dingtalk_bot.py`（钉钉机器人）——全部用 Python 实现。

---

### 2. FastAPI — Web 框架

**说明：** 现代 Python Web 框架，自动生成 OpenAPI 文档，异步原生支持，性能接近 Go/Node.js。比 Flask 快，比 Django 轻。

**项目案例：** `main.py` 中用 `FastAPI()` 创建应用，提供 Web 页面 (`http://localhost:8000`) 和 API 端点。自动文档在 `http://localhost:8000/docs`。

```python
app = FastAPI(title="恩特小助手")
@app.get("/")
async def home(): return HTMLResponse(HOME_HTML)
```

---

### 3. Uvicorn — ASGI 服务器

**说明：** Python 异步 Web 服务器，专门为 FastAPI 这类 ASGI 框架设计。负责接收 HTTP 请求并转给 FastAPI 处理。

**项目案例：** `main.py` 中 `uvicorn.run(app, host="0.0.0.0", port=8000)` 启动服务。

---

### 4. ChromaDB — 向量数据库

**说明：** 轻量级向量数据库，专为 AI 应用设计。核心能力是存储文本的"语义向量"并做相似度搜索。与 Pinecone、Milvus、Qdrant 同属向量数据库赛道，Chroma 是其中最小、最易用的。

**项目案例：** `knowledge_base/` 目录存有 133 条故障记录的向量索引。用户搜"逆变器报错了"，Chroma 能找到语义最接近的故障记录（如"急停告警"），用关键词匹配做不到。

```python
from chromadb import PersistentClient
client = PersistentClient(path="knowledge_base")
collection = client.get_collection("error_codes")
results = collection.query(query_texts=["逆变器报错了"], n_results=3)
```

---

### 5. BAAI/bge-small-zh-v1.5 — 中文嵌入模型

**说明：** 北京智源研究院（BAAI）出品的中文文本向量模型。把中文句子变成 512 维数字向量，"意思相近的句子向量距离近"。大小仅 30MB，CPU 就能跑。

**项目案例：** Chroma 的 embedding function，每一条故障记录入库前被转为向量，搜索时用户的问句也被转向量，然后算余弦相似度找最近邻。

---

### 6. Sentence-Transformers — 嵌入模型框架

**说明：** HuggingFace 出品的句子向量化工具包，统一了加载各种 embedding 模型的方式。

**项目案例：** `sync_kb.py` 中 `SentenceTransformerEmbeddingFunction(model_name="BAAI/bge-small-zh-v1.5")`。

---

### 7. DeepSeek API — 大语言模型

**说明：** 国产大模型 API，项目用的是 deepseek-v4-flash 模型。提供对话、Function Calling 等能力。价格远低于 GPT-4，中文能力优秀。

**项目案例：** RAG Agent 中，用户自然语言问题由 DeepSeek 理解，Agent 判断要不要查知识库、怎么回复。纯聊天问题也用它。

```python
response = client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=[...],
    tools=[...]  # Function Calling
)
```

---

### 8. Function Calling（工具调用） — LLM 调用外部工具

**说明：** 让 LLM 不仅能聊天，还能调用你定义的函数/工具。LLM 自主判断"这个问题我需要查知识库"，然后生成一个工具调用请求，代码收到后执行并返回结果。

**项目案例：** `agent.py` 中定义了 `search_knowledge_base` 工具，DeepSeek 遇到故障相关问题会自动调用它。V4 模型限制 `tool_choice="auto"`（不能强制指定工具）。

```python
TOOLS = [{
    "type": "function",
    "function": {
        "name": "search_knowledge_base",
        "description": "搜索PCS故障知识库",
        "parameters": {...}
    }
}]
```

---

### 9. RAG（检索增强生成） — 架构模式

**说明：** 让 LLM 先查知识库再回答，而不是凭空生成。解决 LLM 编造答案（幻觉）和知识过时的问题。是目前 AI 应用最主流的架构之一。

**项目案例：** 整个恩特小助手的核心架构。用户提问 → Chroma 检索 → 找到相关故障记录 → 注入 LLM 上下文 → LLM 组织回答。三步走：**检索 → 增强 → 生成**。

```
用户问 "d4-1 是什么"
  → Chroma 查到故障记录（检索）
  → 把记录内容拼进 prompt（增强）
  → DeepSeek 用这些信息回答（生成）
```

---

### 10. dingtalk-stream — 钉钉 Stream 模式 SDK

**说明：** 钉钉的新一代机器人接入方式。传统 Webhook 需要公网 IP 让钉钉来连你（像开个门等别人敲门）；Stream 模式是你主动连钉钉（像拉一根专线到钉钉总部），不需要公网 IP。

**项目案例：** 钉钉搜索「恩特小助手」→ 进入单聊 → 发消息 → WebSocket 长连接传给我们 → 处理后回复。

```python
from dingtalk_stream import ChatbotHandler

class MyHandler(ChatbotHandler):
    async def process(self, callback: ChatbotCallback):
        msg = callback.message.content
        reply = agent.handle(msg, user_id)
        self.reply_markdown(reply)  # 同步方法，不要 await
```

---

### 11. openpyxl — Excel 处理库

**说明：** 纯 Python 读写 Excel `.xlsx` 文件的库，不依赖 Office。

**项目案例：** `doc_mgr/extractors/excel.py` 中用 `openpyxl.load_workbook()` 读取 PCS 参数表 V1.6.2.xlsx，从「遥信（DI）」sheet 第 51~183 行提取故障数据。

---

### 12. PyMuPDF (fitz) — PDF 文本提取 + 结构分析

**说明：** 轻量级 PDF 处理库，可以直接提取文字、分析段落结构（字体、字号、位置）。比 `pdfminer` 快，比 `PDFPlumber` 支持格式多。v1.2.1 引入替代 Unstructured。

**项目案例：** `doc_mgr/extractors/pdf_mupdf.py` 提取 PDF 文本；`doc_mgr/chunkers/pymupdf_chunker.py` 做结构分析切块（多信号融合：章节号模式 + 字体名 + 左边界）。

---

### 13. ChromaDB — 向量数据库（补充：多集合支持）

**说明：** 轻量级向量数据库，支持**多个 Collection** 隔离不同知识库。与 Pinecone、Milvus、Qdrant 同属向量数据库赛道，Chroma 是其中最小、最易用的。

**项目案例：** 当前有两个 Collection：
- `error_codes` — 133 条 PCS 故障记录
- `standards` — 标准文档 PDF（含 MinerU OCR 提取的 Markdown）

---

### 14. VectorStore 存储抽象层 — 可切换的存储架构

**说明：** v1.2.1 引入的存储抽象层，定义 `VectorStore` ABC（抽象基类），当前实现 `ChromaStore`。后续切换到 Qdrant 只需在 `storage.py` 改一行注册。

**项目案例：** `doc_mgr/storage.py` 中的 `VectorStore` 接口，统一了 `add/search/delete/list` 操作，与具体向量库解耦。

```python
class VectorStore(ABC):
    @abstractmethod
    def add(self, documents, ids, metadatas=None): ...
    @abstractmethod
    def search(self, query, n_results=5): ...
    @abstractmethod
    def delete(self, ids): ...
```

---

### 15. MinerU（大模型视觉识别）— 扫描 PDF OCR 引擎

**说明：** v1.2.2 引入的 OCR 技术栈升级。MinerU 调用 VLM（视觉大模型）识别扫描 PDF 中的文字、表格、图片，输出结构化 Markdown。替代传统 OCR 路线（PaddleOCR），对复杂排版（多栏、表格、公式）效果更好。

**项目案例：** `scripts/mineru_extract.py` 上传 PDF → MinerU API → VLM 识别 → 下载 ZIP/Markdown；`scripts/sync_mineru.py` 将 Markdown 批量入库。已处理 9 份标准 PDF（GB/T 34133、EN50178、IEC 60664-1 等）。

---

### 16. MarkdownChunker — Markdown 标题层级切块器

**说明：** v1.2.2 新增的切块器，用于处理 MinerU 输出的 Markdown 文件。按 `#` `##` `###` 标题层级智能切块，支持中文标准章节号提取。

**项目案例：** `doc_mgr/chunkers/markdown_chunker.py`，与 PyMuPDFChunker 并列，引擎按文件类型自动选择切块器。

---

### 17. file_handler.py — 钉钉文件接收模块

**说明：** v1.2.3 新增，接收钉钉用户发送的文件（PDF/Excel/图片），自动下载到 `data/uploads/用户名_ID/日期/`。复用 `dingtalk_stream` SDK 的下载方法，无需额外依赖。

**项目案例：** `scripts/file_handler.py`，支持文本/文件/图片三种消息类型，通过 `sender_id + sender_nick` 区分用户。

---

### 18. doc_mgr 文档管理子系统 — 统一文档生命周期管理

**说明：** v1.2.1 引入的独立子系统，将文档的**上传→提取→切块→入库**全流程统一管理。内置 Web 管理页面（`/admin`），支持文件归属类型选择、搜索测试、删除管理。

**项目案例：** `scripts/doc_mgr/` 目录，包含 `engine.py`（处理引擎）、`router.py`（API 路由 6 端点）、`views.py`（管理界面三 Tab）。

```
上传文件 → 自动识别类型 → 提取文本 → 智能切块 → Chroma 入库
```

---

### 19. Git & GitHub — 版本控制

**说明：** Git 是本地版本管理（时光机），GitHub 是云端备份（网盘+协作平台）。

**项目案例：** 整个项目在 Git 管理下，打了 v1.0 / v1.1 / v1.2.1 / v1.2.2 / v1.2.3 标签，每次提交记录变更，推送到 GitHub 远程仓库。国内环境通过本地 Clash 代理（127.0.0.1:7890）直连原生 GitHub 地址推送。

---

### 20. HNSW 索引 — 近似最近邻搜索算法

**说明：** Hierarchical Navigable Small World，向量数据库的核心索引算法。把高维向量组织成多层图结构，搜索时"从粗到细"快速定位最近邻。

**项目案例：** Chroma 内部的默认索引算法，`metadata={"hnsw:space": "cosine"}` 指定用余弦相似度。

---

### 21. 宝塔面板 — 服务器运维面板

**说明：** Linux 服务器可视化运维工具，通过网页管理网站、数据库、防火墙等。对不熟悉 Linux 命令行的用户非常友好。

**项目案例：** Ubuntu 22.04 云服务器 + 宝塔面板，部署恩特小助手实现 7×24 在线。

---

### 22. Logging — Python 日志系统

**说明：** 替代 `print()` 的正规日志方案，支持分级（DEBUG/INFO/WARNING/ERROR）、输出到文件+控制台、格式化时间戳。

**项目案例：** 所有模块都用 `logging.getLogger()`，`mineru_extract.py` 和 `sync_mineru.py` 在 v1.2.3 统一从 print 迁移到 logging。

---

### 23. Jinja2 / HTML 模板 — Web 前端渲染

**说明：** 服务端渲染 HTML 页面，FastAPI 内嵌 HTML 模板，适合简单的 Web 界面。

**项目案例：** `web_page.py` 中聊天风格的 HTML 页面，包含 CSS + JavaScript 的完整交互界面。

---

## 二、未来可学技术（规划/调研中）

### 1. SQLite — 轻量关系数据库

**说明：** 嵌入式关系数据库，Python 自带 `sqlite3` 模块，零部署。支持 SQL 查询、事务（ACID）、索引。单文件存储，备份方便。

**项目场景：** **计划替换 JSON 文件存聊天记忆。** 有了 SQLite，可以"查用户最近 7 天的聊天记录""按关键词搜索历史对话"，JSON 做不到这些。

```python
import sqlite3
conn = sqlite3.connect("data/memory.db")
conn.execute("INSERT INTO memories VALUES (?, ?, ?)", (user_id, role, content))
```

---

### 2. PostgreSQL — 企业级关系数据库

**说明：** 功能最完善的开源关系数据库，支持高并发、JSON 字段、地理空间查询、全文搜索等。是工业界事实标准。

**项目场景：** 当用户量上涨、需要多服务实例时，从 SQLite 平滑迁移到 PostgreSQL。SQL 高度兼容，改个连接字符串基本就能用。

---

### 3. Milvus / Qdrant — 企业级向量数据库

**说明：** Chroma 的"大哥"，支持分布式部署、GPU 加速、每秒处理百万级向量搜索。适合数据量超大的场景。

**项目场景：** 知识库扩展到多个 PCS 参数表 + 产品文档 + 经验记录，数据量到十万级以上时考虑迁移。**目前存储抽象层（VectorStore ABC → ChromaStore）已就位，切换只需改 `storage.py` 一行注册。**

---

### 4. MCP（Model Context Protocol） — AI 工具协议

**说明：** Anthropic 推出的标准化 AI 工具协议，类似 AI 界的 USB 接口。任何 MCP 服务器都可以被任何 MCP 客户端（如 Claude、Cursor）调用。

**项目场景：** 让恩特小助手通过 MCP 协议对接钉钉文档、飞书、企业内部系统，无需单独写每个平台的对接代码。

---

### 5. GitHub Actions — CI/CD 自动化

**说明：** GitHub 自带的自动化流水线。代码推送后自动跑测试、自动部署到服务器。

**项目场景：** 每次 `git push` 后自动：
1. 运行测试
2. 构建 Docker 镜像
3. 部署到云服务器

---

### 6. React / Vue — 前端框架

**说明：** 现代前端框架，构建交互式 Web 界面。React 由 Facebook 维护，Vue 是国产框架（尤雨溪），学习曲线更平缓。

**项目场景：** 升级 Web 页面，从简单的 HTML 内联页面变为 SPA（单页应用），支持实时流式对话、消息气泡、对话历史侧边栏等。

---

### 7. Reranker（重排序模型） — 搜索精度优化

**说明：** 两阶段检索：第一阶段用 embedding 粗召回（快，但精度一般），第二阶段用交叉编码器精排序（慢，但精度极高）。bge-reranker 是 BAAI 出品的重排序模型。

**项目场景：** 当前 Chroma 一次检索可能返回不精确的结果。加上 Reranker 后先取 Top 20，再精排取 Top 3，精度大幅提升。

```
用户提问 → Chroma 粗召回 20 条 → Reranker 精排取 Top 3 → LLM 回答
```

---

### 8. WebSocket — 实时通信

**说明：** 浏览器和服务器之间的长连接，服务器可以主动推数据给浏览器。不像 HTTP 轮询（定时发请求），WebSocket 是"有消息就推"。

**项目场景：** 实现 Web 页面的流式打字机效果（一个字一个字出现），类似 ChatGPT 的体验。当前是等 LLM 全部生成完才一次性显示。

---

### 9. LangChain / LlamaIndex — LLM 框架

**说明：** LangChain 是 LLM 应用开发框架，封装了 RAG、Agent、Chain 等常见模式。LlamaIndex 专注数据索引和检索（RAG 增强）。

**项目场景：** 后续 Agent 工具数量增多时（查故障 + 查经验 + 查文档 + 查天气...），用 LangChain 的 Agent 框架来管理工具更方便。

---

### 10. Redis — 内存缓存 / 消息队列

**说明：** 纯内存数据库，读写微秒级。可以用作缓存（加速查询）、消息队列（异步任务）、会话存储（登录状态）。

**项目场景：**
- **缓存：** 热门故障代码查询结果缓存，不用每次都查 Chroma
- **队列：** 钉钉消息异步处理，先回"收到"再慢慢查

---

### 11. MongoDB — 文档数据库

**说明：** NoSQL 数据库，存 JSON 文档。天然适合聊天记录、日志等半结构化数据。水平扩展容易（分片）。

**项目场景：** 聊天记忆量大时，MongoDB 比 SQLite 更适合，因为消息记录天然就是文档结构。

---

### 12. Kubernetes — 容器编排

**说明：** 容器管理平台，自动部署、扩展、管理容器化应用。当前最主流的云原生基础设施。

**项目场景：** 当服务拆成多个组件（Web 服务、知识库、记忆服务、日志服务）且需要自动扩容时，用 K8s 管理。

---

### 13. Prometheus + Grafana — 监控体系

**说明：** Prometheus 采集指标数据（请求量、延迟、错误率），Grafana 可视化展示仪表盘。

**项目场景：** 监控钉钉机器人调用量、LLM API 耗时、Chroma 查询速度，及时发现性能问题。

---

## 三、技术栈演进路线图

```
现在 (v1.2.3)
├── 编程语言：Python
├── Web框架：FastAPI + Uvicorn
├── 知识库：Chroma + bge-small-zh（多集合：error_codes + standards）
├── LLM：DeepSeek API (Function Calling)
├── Agent：手写 RAG Agent（多工具路由）
├── 记忆：JSON 文件（临时）
├── 部署：Docker / 宝塔面板
├── 版本：Git + GitHub（v1.0~v1.2.3）
├── 消息：钉钉 Stream
├── PDF提取：PyMuPDF 结构分析切块 / MinerU 大模型视觉OCR
├── 文档管理：doc_mgr 子系统（存储抽象层，web 管理页面）
├── 文件接收：钉钉文件/图片自动下载保存
└── 日志：Logging

      ↓

近期规划
├── 记忆：JSON → SQLite ← 🔜 最近要做的
├── 搜索：加入 Reranker 精排（可选）
└── Web UI：升级交互体验（可选）

      ↓

中期规划
├── 知识库：多集合（故障 + 文档 + 经验）
├── Agent：LangChain 管理多工具
├── 前端：React / Vue 升级
├── 通信：WebSocket 流式输出
├── 缓存：Redis
└── 监控：Prometheus + Grafana

      ↓

远期规划
├── 存储：SQLite → PostgreSQL
├── 向量库：Chroma → Milvus / Qdrant
├── 部署：Docker → Kubernetes
├── 自动化：GitHub Actions CI/CD
├── 工具协议：MCP 标准化
└── 架构：单体 → 微服务
```

---

## 一句话总结

> **学到手的：** Python + FastAPI + Chroma + DeepSeek + RAG + Docker + PyMuPDF + MinerU + Git — 已经能独立搭一个包含故障查询、标准文档检索、钉钉交互的 AI 应用从开发到上线。
>
> **下一步重点：** SQLite + WebSocket + 前端框架 — 补齐存储、实时、界面三个短板。

---

*恩特能源 · 恩特小助手 · 2026-07-14 · v1.2.3*
