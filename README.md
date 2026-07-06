# 🔧 恩特小助手 — PCS 故障代码智能查询系统

> 恩特能源内部工具 — 基于 RAG 架构的故障代码查询机器人

---

## 📋 功能概览

| 功能 | 状态 | 说明 |
|------|------|------|
| Web 页面查询 | ✅ 已上线 | http://localhost:8000 |
| 钉钉机器人查询 | ✅ 已上线 | 群内 @恩特小助手 |
| 精确故障代码匹配 | ✅ 已完成 | 秒回，不调 LLM |
| 语义模糊搜索 | ✅ 已完成 | 支持自然语言/名称/原因 |
| 局域网共享 | ✅ 已完成 | 同 WiFi 可访问 |
| PCS 参数表对接 | ✅ 已完成 | 133 条实际工程数据 |

---

## 🏗️ 系统架构

```
┌─────────────────────────────────────────────────────┐
│                   用户入口                            │
│    Web 页面 (localhost:8000)   钉钉群聊 @机器人      │
└─────────────────────────┬───────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────┐
│               FastAPI 服务 (main.py)                  │
│                                                       │
│    ┌─────────────────────────────────────────────┐   │
│    │          error_query.handle(q)               │   │
│    │                                              │   │
│    │  提问 ──→ 含故障代码？──→ 精确匹配（秒回）    │   │
│    │            │                                 │   │
│    │            └──→ 语义搜索 Chroma → 返回结果   │   │
│    │            │                                 │   │
│    │            └──→ 复杂 NL → LLM 提取 → 搜索   │   │
│    └─────────────────────────────────────────────┘   │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│              Chroma 向量数据库                        │
│  模型：BAAI/bge-small-zh-v1.5                       │
│  数据：133 条故障记录（PCS参数表 遥信DI sheet）      │
└─────────────────────────────────────────────────────┘
```

### 查询策略

| 输入类型 | 处理方式 | LLM? | 速度 |
|---------|---------|------|------|
| `d4-1`、`df-8`（故障代码） | Chroma 元数据精确匹配 | ❌ | ⚡ 毫秒 |
| `急停告警`（名称） | Chroma 语义搜索 | ❌ | ⚡ 毫秒 |
| `外部急停信号闭合`（原因） | Chroma 语义搜索 | ❌ | ⚡ 毫秒 |
| `这个逆变器报错了怎么办`（自然语言） | LLM 提取关键词 → 语义搜索 | ✅ 仅提取 | ⚡ 较快 |
| `硬件`（关键词） | Chroma 语义搜索 | ❌ | ⚡ 毫秒 |

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
# DeepSeek API Key（必填，用于自然语言提取）
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
```

### 局域网共享

```bash
# 以管理员身份运行
netsh advfirewall firewall add rule name="恩特小助手 8000" dir=in action=allow protocol=TCP localport=8000
```

其他设备访问：`http://[本机IP]:8000`

---

## 📁 项目结构

```
D:\ENTAR_AGENT\
├── CLAUDE.md                          # 项目级指南（给 AI 使用）
├── README.md                          # 本文件
├── data/
│   └── PCS参数表 V1.6.2.xlsx          # ⚡ 实际工程 PCS 参数表
├── knowledge_base/                    # 💾 Chroma 向量库（自动生成）
├── scripts/
│   ├── local_config.py                # 🔑 凭证配置（已 gitignore）
│   ├── main.py                        # 🚀 FastAPI 服务入口
│   ├── sync_kb.py                     # 🔄 知识库同步脚本
│   └── skills/
│       ├── error_query.py             # 🔧 故障查询核心逻辑
│       └── dingtalk_bot.py            # 🤖 钉钉机器人
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
error_query.handle() → Chroma / DeepSeek
    ↓
reply_markdown() (通过 session_webhook 回复)
    ↓
钉钉群聊
```

### 部署步骤

1. **钉钉开发者后台** → 创建企业内部应用
2. **开启机器人能力** → 选择 Stream Mode
3. **复制 ClientID + ClientSecret** → 填入 `local_config.py`
4. **创建版本并发布**（管理员可设免审批）
5. **安装应用到组织**
6. **群设置 → 机器人 → 添加机器人** → 找到「恩特小助手」

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

## ⚠️ 已知问题

1. **息屏断连** — 服务跑在本地电脑，息屏/睡眠会断开钉钉连接，建议部署到云服务器
2. **短关键词语义搜索** — 极短关键词（2字以下）匹配效果可能不理想
3. **Windows GBK 编码** — 控制台输出 emoji 可能报错

---

## 📌 路线图

- [x] 故障代码精确匹配（秒回）
- [x] 语义模糊搜索
- [x] 钉钉机器人上线
- [x] PCS 实际工程数据接入
- [ ] 云服务器部署（7×24 小时在线）
- [ ] 其他 PCS 参数表 sheet 对接
- [ ] 经验查询功能
- [ ] 钉钉知识库集成

---

*恩特能源 · 内部工具*
