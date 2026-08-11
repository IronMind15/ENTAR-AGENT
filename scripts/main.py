"""
恩特小助手 — 统一入口
通过技能注册中心路由到不同技能模块
"""

import logging
import os
import sys
import threading
from logging.handlers import TimedRotatingFileHandler
sys.path.insert(0, os.path.dirname(__file__))

# 钉钉 SDK（dingtalk_stream）内部用 requests 且默认 trust_env=True 跟随系统代理。
# 本机 Clash 开启时会把 api.dingtalk.com 劫持到 127.0.0.1:7890 → TLS 握手被中断
# （SSL: UNEXPECTED_EOF_WHILE_READING），与 v1.5.6「国内 API 全直连」同源问题。
# 设置 NO_PROXY 让 requests 对钉钉域名直连绕过代理（不影响本机其他流量）。
# 注：服务器上无 Clash，此设置无害（NO_PROXY 仅在走代理时生效）。
os.environ.setdefault("NO_PROXY", "api.dingtalk.com,oapi.dingtalk.com,wss-open-connection-union.dingtalk.com")
os.environ.setdefault("no_proxy", "api.dingtalk.com,oapi.dingtalk.com,dingtalk.com,wss-open-connection-union.dingtalk.com")

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
        TimedRotatingFileHandler(
            os.path.join(_LOG_DIR, "entark.log"),
            encoding="utf-8",
            when="midnight",   # 每天 0 点按天轮转
            backupCount=7,     # 保留最近 7 天，更早自动删除
        ),
    ],
)
logger = logging.getLogger("main")

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

from skills import get_matched_skill, get_skill_list
from skills.dingtalk_bot import start_bot as start_dingtalk_bot
from web_page import HOME_HTML
from doc_mgr.router import router as admin_router
from doc_mgr.scheduler import start_scheduler, stop_scheduler
from dashboard_scheduler import start_dashboard_scheduler, stop_dashboard_scheduler

app = FastAPI(title="恩特小助手")
app.include_router(admin_router)


@app.on_event("startup")
def _startup():
    """启动时：先崩溃恢复（同步），再后台预热重排模型。

    顺序执行避免并发触发 numpy 循环导入（chromadb 与 sentence_transformers
    都依赖 numpy，两线程同时 import 会 circular import 失败）。
    """
    try:
        from doc_mgr.recovery import recover_crashed_data
        from doc_mgr.storage import get_store
        recovered = recover_crashed_data(get_store())
        changed = sum(
            1 for s in recovered.values()
            if s.get("deleted") or s.get("restored")
        )
        if changed:
            logger.info(f"  [恢复] 崩溃恢复完成: {recovered}")
        else:
            logger.info("  [恢复] 无遗留 staging/retired 数据")
    except Exception:
        logger.exception("  [恢复] 崩溃恢复失败（不影响启动）")

    # 预热重排模型（recovery 完成后再启动线程，sleep 避开 numpy 导入窗口）
    def _preload_reranker():
        try:
            import time
            time.sleep(1)
            from skills.enhanced_search import _get_reranker
            _get_reranker()
            logger.info("  已预热 bge-reranker 重排模型")
        except Exception as e:
            logger.warning(f"重排模型预热失败（不影响启动）: {e}")

    threading.Thread(target=_preload_reranker, daemon=True).start()


@app.get("/", response_class=HTMLResponse)
def home():
    """返回聊天风格 Web 页面（HTML/JS 定义在 web_page.py 中）"""
    return HOME_HTML


@app.post("/ask")
def ask(q: str = Form("", description="用户问题"), user: str = Form("", description="用户名（仅记忆归属，不做权限依据）")):
    """统一问答接口 — 通过技能注册中心路由

    P1 安全收尾：GET → POST，避免问题内容进入 URL、浏览器历史和代理日志。
    Web 的 user 参数仅用于会话记忆归属，不作为任何权限依据；
    敏感操作（上传/删除/同步）一律走 /admin 接口的 password 或 user_store 鉴权。
    """
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
            memory.add(uid, "user", q)
            memory.add(uid, "assistant", answer)
        except Exception:
            pass

    return JSONResponse(result)


@app.post("/feedback")
def feedback(
    q: str = Form("", description="用户问题"),
    answer: str = Form("", description="回答摘要"),
    source: str = Form("", description="来源标识"),
    rating: str = Form(..., description="up 或 down"),
    user: str = Form("", description="用户名"),
):
    """用户反馈接口 — 👍/👎"""
    if rating not in ("up", "down"):
        return JSONResponse({"error": "rating 必须是 up 或 down"}, status_code=400)

    user_id = f"web_{user}" if user else "anonymous"
    try:
        from user_store import add_feedback
        add_feedback(user_id, q, answer, source, rating)
        logger.info(f"[反馈] {user_id} {rating} — {q[:50]}")
        return JSONResponse({"ok": True})
    except Exception as e:
        logger.error(f"反馈写入失败: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


if __name__ == "__main__":
    import atexit
    port = int(os.environ.get("PORT", 8000))

    skill_names = ", ".join(s.name for s in get_skill_list())
    logger.info(f"🔧 恩特小助手启动：http://localhost:{port}")
    logger.info(f"  已注册技能: {skill_names}")

    # 启动钉钉机器人（后台线程）
    start_dingtalk_bot()

    # 启动后台自动同步调度器（当前已禁用，全部走管理员手动入库）
    # 如需重新启用，取消下面两行注释
    # start_scheduler()
    # atexit.register(stop_scheduler)
    logger.info(f"  自动同步已禁用（手动模式）：管理员在 /admin 后台操作入库")

    # 启动看板定时推送调度器（v1.11.0）：每分钟扫描到点订阅，主动推送每日项目看板
    start_dashboard_scheduler()
    atexit.register(stop_dashboard_scheduler)
    logger.info(f"  看板定时推送调度器已启动（无订阅不推送）")

    logger.info(f"  将来扩展: 添加新技能 → 新建 skills/*.py + __init__.py 一行注册")
    uvicorn.run(app, host="0.0.0.0", port=port)
