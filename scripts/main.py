"""
恩特小助手 — 统一入口
通过技能注册中心路由到不同技能模块
"""

import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

# Windows UTF-8
if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

from skills import get_matched_skill, get_skill_list
from skills.dingtalk_bot import start_bot as start_dingtalk_bot

app = FastAPI(title="恩特小助手")


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
                document.getElementById('result').innerHTML = data.answer.replace(/\\n/g, '<br>');
            }
        </script>
    </body>
    </html>
    """


@app.get("/ask")
def ask(q: str = Query("", description="用户问题")):
    """统一问答接口 — 通过技能注册中心路由"""
    if not q:
        return JSONResponse({"answer": "请输入问题"})

    print(f"[query] {q}")

    # 遍历已注册技能，找到第一个匹配的处理
    skill_cls = get_matched_skill(q)
    if skill_cls:
        print(f"  → {skill_cls.name}")
        result = skill_cls.handle(q)
    else:
        # 理论上不会走到这里（chat 始终匹配），保留兜底
        from skills import chat
        print(f"  → chat (fallback)")
        result = chat.handle(q)

    return JSONResponse(result)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))

    skill_names = ", ".join(s.name for s in get_skill_list())
    print(f"\n🔧 恩特小助手启动：http://localhost:{port}")
    print(f"  已注册技能: {skill_names}")

    # 启动钉钉机器人（后台线程）
    start_dingtalk_bot()

    print(f"  将来扩展: 添加新技能 → 新建 skills/*.py + __init__.py 一行注册")
    print()
    uvicorn.run(app, host="0.0.0.0", port=port)
