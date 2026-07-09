"""
恩特小助手 — 统一入口
通过技能注册中心路由到不同技能模块
"""

import logging
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

# 统一日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")

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
        <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
        <title>恩特小助手</title>
        <style>
            body { font-family: sans-serif; max-width: 700px; margin: 40px auto; padding: 0 20px; }
            .user-info { background: #f0f7ff; padding: 10px 15px; border-radius: 8px; margin-bottom: 15px; display: flex; align-items: center; gap: 10px; }
            .user-info label { font-size: 14px; color: #555; }
            .user-info input { flex: 1; padding: 6px 10px; font-size: 14px; border: 1px solid #ddd; border-radius: 4px; margin: 0; }
            input, textarea { width: 100%; padding: 10px; font-size: 16px; margin: 10px 0; box-sizing: border-box; }
            button { padding: 10px 30px; font-size: 16px; cursor: pointer; }
            .result { background: #f5f5f5; padding: 15px; border-radius: 8px; margin: 20px 0; white-space: pre-wrap; }
            .tag { display: inline-block; background: #e0e0e0; padding: 2px 10px; border-radius: 12px; font-size: 12px; margin-bottom: 10px; }
            .note { font-size: 12px; color: #999; margin-top: 10px; }
        </style>
    </head>
    <body>
        <h2>🔧 恩特小助手</h2>
        <p>输入故障代码/描述查询原因，或直接提问聊天</p>
        <div class="user-info">
            <label>👤 你的名字</label>
            <input type="text" id="username" placeholder="输入名字以保存对话记忆">
            <button onclick="saveName()" style="padding: 6px 15px; font-size: 13px;">保存</button>
        </div>
        <input type="text" id="query" placeholder="例如：d4-1、急停、今天天气怎么样">
        <button onclick="search()">查询</button>
        <div id="loading" style="display:none; color:#666;">查询中...</div>
        <div id="result" class="result">输入后点击查询</div>
        <div class="note">💡 输入名字后，小助手会记住你聊过的内容（刷新页面不丢失）</div>
        <script>
            var _searching = false;  // 防重复提交
            // 加载已保存的名字
            window.onload = function() {
                var saved = localStorage.getItem('entar_username');
                if (saved) document.getElementById('username').value = saved;
            };
            function saveName() {
                var name = document.getElementById('username').value.trim();
                if (name) {
                    localStorage.setItem('entar_username', name);
                    alert('名字已保存，对话记忆将关联到"' + name + '"');
                }
            }
            async function search() {
                if (_searching) return;
                _searching = true;
                var q = document.getElementById('query').value;
                var user = document.getElementById('username').value.trim();
                if (!user) {
                    alert('请先输入你的名字，以便保存对话记忆');
                    return;
                }
                localStorage.setItem('entar_username', user);
                document.getElementById('loading').style.display = 'block';
                document.getElementById('result').innerHTML = '';
                var url = '/ask?q=' + encodeURIComponent(q) + '&user=' + encodeURIComponent(user);
                var res = await fetch(url);
                var data = await res.json();
                document.getElementById('loading').style.display = 'none';
                document.getElementById('result').innerHTML = data.answer.replace(/\\n/g, '<br>');
                _searching = false;
            }
            // 按回车查询
            document.getElementById('query').addEventListener('keydown', function(e) {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    search();
                }
            });
        </script>
    </body>
    </html>
    """


@app.get("/ask")
def ask(q: str = Query("", description="用户问题"), user: str = Query("", description="用户名")):
    """统一问答接口 — 通过技能注册中心路由"""
    if not q:
        return JSONResponse({"answer": "请输入问题"})

    logger.info(f"[{user or '匿名'}] {q}")

    # 遍历已注册技能，找到第一个匹配的处理
    user_id = f"web_{user}" if user else ""

    skill_cls = get_matched_skill(q)
    if skill_cls:
        logger.info(f"  → {skill_cls.name}")
        # 如果是聊天技能，传入 user_id 以便注入记忆
        if skill_cls.name == "通用聊天" and user:
            result = skill_cls.handle(q, user_id=user_id)
        else:
            result = skill_cls.handle(q)
    else:
        from skills import chat
        logger.info(f"  → chat (fallback)")
        result = chat.handle(q, user_id=user_id) if user else chat.handle(q)

    # Web 页面用户：记录对话到记忆
    if user:
        try:
            from skills import memory
            uid = f"web_{user}"
            answer = result.get("answer", "")
            memory.add(uid, "user", q[:500])
            memory.add(uid, "assistant", answer[:500])
        except Exception:
            pass

    return JSONResponse(result)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))

    skill_names = ", ".join(s.name for s in get_skill_list())
    logger.info(f"🔧 恩特小助手启动：http://localhost:{port}")
    logger.info(f"  已注册技能: {skill_names}")

    # 启动钉钉机器人（后台线程）
    start_dingtalk_bot()

    logger.info(f"  将来扩展: 添加新技能 → 新建 skills/*.py + __init__.py 一行注册")
    uvicorn.run(app, host="0.0.0.0", port=port)
