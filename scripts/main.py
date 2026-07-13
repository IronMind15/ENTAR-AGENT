"""
恩特小助手 — 统一入口
通过技能注册中心路由到不同技能模块
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
sys.path.insert(0, os.path.dirname(__file__))

# Windows 终端 UTF-8（必须在日志配置之前，否则 StreamHandler 拿到 GBK 句柄）
if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    os.environ["PYTHONIOENCODING"] = "utf-8"

# 日志目录
_LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(_LOG_DIR, exist_ok=True)

# 统一日志配置：同时输出控制台 + 文件
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),                                              # 控制台
        RotatingFileHandler(
            os.path.join(_LOG_DIR, "entark.log"),
            encoding="utf-8",
            maxBytes=5 * 1024 * 1024,   # 5MB 轮转
            backupCount=3,
        ),
    ],
)
logger = logging.getLogger("main")

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

from skills import get_matched_skill, get_skill_list
from skills.dingtalk_bot import start_bot as start_dingtalk_bot
from web_page import HOME_HTML
from doc_mgr.router import router as admin_router

app = FastAPI(title="恩特小助手")
app.include_router(admin_router)


@app.get("/", response_class=HTMLResponse)
def home():
    """返回聊天风格 Web 页面（HTML/JS 定义在 web_page.py 中）"""
    return HOME_HTML


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
        kwargs = {}
        if user:
            kwargs["user_id"] = user_id
        result = skill_cls.handle(q, **kwargs)
    else:
        # 理论上不会走到这里（Agent 始终匹配），防御性兜底
        logger.info(f"  → 备用处理")
        result = {"answer": f"抱歉，我暂时无法处理这个问题。", "source": "fallback"}

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
