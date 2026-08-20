"""
恩特小助手 — 统一入口
通过技能注册中心路由到不同技能模块

v1.12.8 起 Web 端砍掉用户层，只做管理层（/admin，普通用户只用钉钉）：
  - / 由聊天页改为跳转 /admin（管理员鉴权在 /admin 自身处理）
  - /ask（Web 问答）、/feedback（Web 反馈）路由已物理删除
  - 钉钉端反馈（dingtalk_bot 的 👍/👎 回调）不受影响，/admin/feedback-stats 保留
  - 恢复指引：若未来重新开放 Web 聊天，从 git history（v1.12.x 及更早）
    找回 /ask、/feedback 实现，并重新引用 web_page.py 的 HOME_HTML
"""

import logging
import os
import sys
import threading
from logging.handlers import TimedRotatingFileHandler

# 兼容历史本地启动方式：python scripts/main.py
# 直接执行时 Python 只把 scripts/ 放进 sys.path，需要补充项目根目录，
# 使后续导入仍然使用唯一的 scripts.* 包名。
if __package__ in {None, ""}:
    _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)

from scripts.paths import LOG_DIR, ensure_runtime_dirs

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
ensure_runtime_dirs()

# 统一日志配置：同时输出控制台 + 文件
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),                                              # 控制台
        TimedRotatingFileHandler(
            os.path.join(str(LOG_DIR), "entark.log"),
            encoding="utf-8",
            when="midnight",   # 每天 0 点按天轮转
            backupCount=7,     # 保留最近 7 天，更早自动删除
        ),
    ],
)
logger = logging.getLogger("main")

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
import uvicorn

from scripts.skills import get_skill_list
from scripts.skills.dingtalk_bot import start_bot as start_dingtalk_bot
from scripts.doc_mgr.router import router as admin_router
from scripts.doc_mgr.scheduler import start_scheduler, stop_scheduler
from scripts.dashboard_scheduler import start_dashboard_scheduler, stop_dashboard_scheduler

app = FastAPI(title="恩特小助手")
app.include_router(admin_router)


def _validate_security_config():
    """生产部署必须显式配置管理端认证，避免漏变量就暴露 /admin。"""
    from scripts.config import APP_ENV, ADMIN_PASSWORD, IS_PRODUCTION
    if IS_PRODUCTION and not ADMIN_PASSWORD:
        raise RuntimeError(
            "ENTAR_ENV=production 时必须设置 ADMIN_PASSWORD；"
            "已拒绝启动，避免 /admin 管理接口无认证暴露。"
        )
    if not ADMIN_PASSWORD:
        logger.warning("管理端当前未设置 ADMIN_PASSWORD，仅允许 development 环境本地使用")
    else:
        logger.info("管理端认证已启用（环境：%s）", APP_ENV)


@app.on_event("startup")
def _startup():
    """启动时：先崩溃恢复（同步），再后台预热重排模型。

    顺序执行避免并发触发 numpy 循环导入（chromadb 与 sentence_transformers
    都依赖 numpy，两线程同时 import 会 circular import 失败）。
    """
    _validate_security_config()
    try:
        from scripts.doc_mgr.recovery import recover_crashed_data
        from scripts.doc_mgr.storage import get_store
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
            from scripts.skills.enhanced_search import _get_reranker
            _get_reranker()
            logger.info("  已预热 bge-reranker 重排模型")
        except Exception as e:
            logger.warning(f"重排模型预热失败（不影响启动）: {e}")

    threading.Thread(target=_preload_reranker, daemon=True).start()


@app.get("/", include_in_schema=False)
def home():
    """根路径 → 管理端（v1.12.8 起 Web 不做用户层，普通用户只用钉钉）"""
    return RedirectResponse(url="/admin", status_code=302)


# ── ⏸️ v1.12.8 已停用 Web 用户层 ─────────────────────────────
# /ask（Web 问答）、/feedback（Web 反馈）路由已物理删除，普通用户只用钉钉单聊。
# 钉钉端反馈（dingtalk_bot 👍/👎）与 /admin/feedback-stats 不受影响。
# 原实现见 git history（v1.12.x）；恢复指引见本文件文件头 docstring。


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
