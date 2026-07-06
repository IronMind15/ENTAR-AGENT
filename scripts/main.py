"""
恩特小助手 — 统一入口
根据意图路由到不同技能模块
"""

import os
import re
import sys
sys.path.insert(0, os.path.dirname(__file__))

# Windows UTF-8
if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

from skills import error_query
from skills import chat
from skills.dingtalk_bot import start_bot as start_dingtalk_bot

app = FastAPI(title="恩特小助手")

# 故障代码正则（与 error_query 保持一致）
_FAULT_CODE_PATTERN = re.compile(r'(d[a-z0-9]+[-~]\d[\d~-]*)', re.IGNORECASE)

# 强故障信号 — 只要包含这些词，一定走故障查询
_FAULT_KEYWORDS = ["故障", "报错", "异常", "告警", "停机", "急停"]

# 非故障的自然语言触发 — 只要包含这些，直接走聊天
_CHAT_TRIGGERS = ["你好", "嗨", "hello", "hi", "你是谁", "你叫什么",
                  "谢谢", "再见", "拜拜", "帮个忙", "帮帮忙", "帮我"]


def _is_fault_related(query: str) -> bool:
    """判断是否与故障相关（强信号）"""
    q = query.strip().lower()
    if not q:
        return False
    # 有故障代码（d4-1 等）
    if _FAULT_CODE_PATTERN.search(query):
        return True
    # 有强故障关键词
    return any(kw in q for kw in _FAULT_KEYWORDS)


def _is_chat_query(query: str) -> bool:
    """判断是否明显为聊天性质的问题"""
    q = query.strip().lower()
    if not q:
        return False
    # 显式触发词
    if any(kw in q for kw in _CHAT_TRIGGERS):
        return True
    # 自然语言特征词
    NL_MARKERS = ["的", "了", "吗", "呢", "吧", "是", "怎么回事", "怎么",
                   "为什么", "如何", "怎么办", "什么", "哪个", "请问",
                   "谁", "可以", "能", "好", "嗯", "哦", "哈"]
    return any(marker in q for marker in NL_MARKERS)


@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>恩特小助手</title>
        <style>
            body { font-family: sans-serif; max-width: 700px; margin: 40px auto; padding: 0 20px; }
            input, textarea { width: 100%; padding: 10px; font-size: 16px; margin: 10px 0; }
            button { padding: 10px 30px; font-size: 16px; cursor: pointer; }
            .result { background: #f5f5f5; padding: 15px; border-radius: 8px; margin: 20px 0; white-space: pre-wrap; }
            .tag { display: inline-block; background: #e0e0e0; padding: 2px 10px; border-radius: 12px; font-size: 12px; margin-bottom: 10px; }
        </style>
    </head>
    <body>
        <h2>🔧 恩特小助手</h2>
        <p>输入故障代码/描述查询原因，或直接提问聊天</p>
        <input type="text" id="query" placeholder="例如：d4-1、急停、今天天气怎么样" onkeydown="if(event.key==='Enter') search()">
        <button onclick="search()">查询</button>
        <div id="loading" style="display:none; color:#666;">查询中...</div>
        <div id="result" class="result">输入后点击查询</div>
        <script>
            async function search() {
                const q = document.getElementById('query').value;
                document.getElementById('loading').style.display = 'block';
                document.getElementById('result').innerHTML = '';
                const res = await fetch('/ask?q=' + encodeURIComponent(q));
                const data = await res.json();
                document.getElementById('loading').style.display = 'none';
                document.getElementById('result').innerHTML = data.answer.replace(/\n/g, '<br>');
            }
        </script>
    </body>
    </html>
    """


@app.get("/ask")
def ask(q: str = Query("", description="用户问题")):
    """统一问答接口"""
    if not q:
        return JSONResponse({"answer": "请输入问题"})

    print(f"[query] {q}")

    # 1. 与故障相关（故障代码、强故障关键词）→ 故障查询
    if _is_fault_related(q):
        print(f"  → error_query")
        result = error_query.handle(q)

    # 2. 其他情况一律走聊天（托底）
    else:
        print(f"  → chat (fallback)")
        result = chat.handle(q)

    return JSONResponse(result)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))

    print(f"\n🔧 恩特小助手启动：http://localhost:{port}")
    print(f"  技能: 故障查询、通用聊天（托底）")

    # 启动钉钉机器人（后台线程）
    start_dingtalk_bot()

    print(f"  将来扩展: 项目经验、多模态...")
    print()
    uvicorn.run(app, host="0.0.0.0", port=port)
