"""
文档管理 API 路由

提供 REST API 供管理页面调用。
所有路由以 /admin 为前缀，由 main.py 挂载。

权限说明：
  - 开发环境未设 ADMIN_PASSWORD 时，保留本地开放兼容；生产环境 fail-closed
  - 设置 ADMIN_PASSWORD 后，通过 POST 登录签发 HttpOnly 会话 Cookie
    （旧 ?password= 参数仅为兼容已有自动化调用，管理页面不再使用）
  - 钉钉端上传由 user_store.check_permission() 控制（基于 leader/role）
"""

import html
import json
import os
import re
import uuid
import logging
import hashlib
import hmac
from contextvars import ContextVar
from datetime import datetime as dt
from fastapi import APIRouter, Depends, UploadFile, File, Form, Query, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from scripts.paths import (DATA_ROOT, EXPERIENCE_DIR, FAULT_CODES_DIR, RUNTIME_DIR,
                   STANDARDS_DIR, UPLOADS_DIR)

from .engine import process_file
from .storage import get_store
from .views import ADMIN_HTML
from .task_manager import get_manager as get_task_manager
from .identity import file_sha256

logger = logging.getLogger("doc_mgr.router")

router = APIRouter(prefix="/admin")

# 文件保存目录映射（按 collection 分类存储）
FILE_DIRS = {
    "standards": str(STANDARDS_DIR),
    "error_codes": str(FAULT_CODES_DIR),
    "experience_kb": str(EXPERIENCE_DIR),
}

# 钉钉上传目录（按用户/日期分类）
UPLOAD_DIR = str(UPLOADS_DIR)

# ===== 上传安全（P1 安全收尾） =====
MAX_UPLOAD_MB = 50
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
_ALLOWED_EXT = {".pdf", ".xlsx", ".xls", ".md"}
_ALLOWED_COLLECTIONS = {"standards", "error_codes", "experience_kb"}

# 数据根目录：sync-delete / sync-trigger 等接受 file_path 的接口
# 只能操作此目录内的文件，防止未授权删除/处理服务器任意路径
_DATA_ROOT = str(DATA_ROOT)
_RUNTIME_ROOT = str(RUNTIME_DIR)
_ADMIN_SESSION_COOKIE = "entar_admin_session"
_ADMIN_SESSION_VALID: ContextVar[bool] = ContextVar(
    "entar_admin_session_valid", default=False)


def _is_within_data_dir(path: str) -> bool:
    """判断路径是否位于项目 data/ 目录内（防任意文件删除/入库）"""
    if not path:
        return False
    try:
        candidate = os.path.abspath(os.path.normpath(path))
        roots = [os.path.abspath(_DATA_ROOT), os.path.abspath(_RUNTIME_ROOT)]
        return any(candidate == root or candidate.startswith(root + os.sep)
                   for root in roots)
    except (OSError, ValueError):
        return False


def _sanitize_filename(name: str) -> str:
    """净化文件名：去路径、去危险字符、限长，保留中文字符"""
    base = os.path.basename(name.replace("\\", "/"))
    base = re.sub(r"[^\w.\-一-鿿（）() ]+", "_", base)
    return (base.strip(" .") or "unnamed")[:120]


def _check_file_signature(filename: str, content: bytes) -> bool:
    """MIME 内容签名校验（双重校验，防伪造扩展名）"""
    ext = os.path.splitext(filename)[1].lower()
    sig = content[:8]
    if ext == ".pdf":
        return sig.startswith(b"%PDF")
    if ext in (".xlsx", ".xls"):
        return sig.startswith(b"PK\x03\x04") or sig.startswith(b"\xd0\xcf\x11\xe0")
    if ext == ".md":
        # 纯文本 Markdown：无空字节 + 有内容（经验库用）
        return b"\x00" not in content[:512] and bool(content.strip())
    return False


def _get_admin_password() -> str:
    """读取配置的管理员密码（空表示不开启）"""
    try:
        import importlib
        cfg = importlib.import_module("scripts.config")
        return getattr(cfg, "ADMIN_PASSWORD", "") or ""
    except Exception:
        return ""


def _admin_session_signature() -> str:
    """从当前管理密码派生不可逆会话标识，不把明文密码存进 Cookie。"""
    password = _get_admin_password()
    if not password:
        return ""
    return hmac.new(
        password.encode("utf-8"), b"entar-admin-session-v1", hashlib.sha256,
    ).hexdigest()


def _has_valid_admin_session(request: Request) -> bool:
    expected = _admin_session_signature()
    supplied = request.cookies.get(_ADMIN_SESSION_COOKIE, "")
    return bool(expected and supplied and hmac.compare_digest(supplied, expected))


async def _bind_admin_session(request: Request):
    """为所有管理端路由绑定当前请求的会话认证结果。"""
    _ADMIN_SESSION_VALID.set(_has_valid_admin_session(request))


def _verify_admin_access(password: str = "") -> bool:
    """验证管理员访问权限"""
    admin_pw = _get_admin_password()
    if not admin_pw:
        # 本地开发兼容：production/prod 必须由 main.py 在启动时拒绝。
        # 这里同样 fail-closed，避免测试外的 ASGI 宿主绕过 main.py 直接挂载 router。
        try:
            from scripts.config import IS_PRODUCTION
            return not IS_PRODUCTION
        except Exception:
            return False
    return bool(_ADMIN_SESSION_VALID.get() or
                (password and hmac.compare_digest(password, admin_pw)))


def _check_password(request: Request) -> str | None:
    """读取兼容旧调用的 query 密码；管理页面自身使用会话 Cookie。"""
    return request.query_params.get("password", "")


router.dependencies.append(Depends(_bind_admin_session))


def _check_user_permission(user_id: str = "", action: str = "upload") -> bool:
    """检查用户是否有权限执行操作"""
    if not user_id:
        return False
    try:
        from scripts.user_store import get_store
        return get_store().check_permission(user_id, action)
    except Exception:
        return False


@router.get("", response_class=HTMLResponse)
def admin_home(request: Request):
    """管理页面首页"""
    password = _check_password(request)
    if not _verify_admin_access(password):
        return HTMLResponse(
            content="<h2>需要密码</h2>"
                     "<form method='post' action='/admin/login'>"
                     "密码：<input name='password' type='password'>"
                     "<input type='submit' value='进入'>"
                     "</form>"
                     "<p>（请联系管理员获取密码）</p>",
            status_code=401,
        )
    return ADMIN_HTML


@router.post("/login")
def admin_login(password: str = Form("")):
    """管理端登录：只接受 POST，签发 HttpOnly 签名 Cookie 后跳转。"""
    if not _verify_admin_access(password):
        return HTMLResponse("<h2>密码错误</h2><a href='/admin'>返回重试</a>",
                            status_code=401)
    response = RedirectResponse(url="/admin", status_code=303)
    try:
        from scripts.config import IS_PRODUCTION
        secure = IS_PRODUCTION
    except Exception:
        secure = False
    response.set_cookie(
        _ADMIN_SESSION_COOKIE, _admin_session_signature(), max_age=8 * 3600,
        httponly=True, samesite="lax", secure=secure, path="/admin",
    )
    return response


@router.get("/collections")
def list_collections(password: str = Query("", description="管理员密码")):
    """列出所有 collection 及其统计数据"""
    if not _verify_admin_access(password):
        raise HTTPException(403, "密码错误，无权访问")
    store = get_store()
    collections = store.list_collections()
    result = {}
    for coll in collections:
        result[coll] = {"count": store.count(coll)}
    return JSONResponse(result)


@router.get("/docs")
def list_docs(collection: str = Query("", description="按 collection 过滤"),
              password: str = Query("", description="管理员密码")):
    """列出已入库文档，按 collection 分组

    从 Chroma 中读取 metadata，按 file_name 字段归组。
    """
    if not _verify_admin_access(password):
        raise HTTPException(403, "密码错误，无权访问")
    try:
        store = get_store()
        collections = store.list_collections()
        result: dict = {}

        for coll in collections:
            if collection and coll != collection:
                continue

            data = store.get(coll)
            if not data or not data.get("metadatas"):
                result[coll] = {"count": 0, "files": []}
                continue

            # 按 file_name 分组
            files: dict = {}
            for meta in data["metadatas"]:
                fn = meta.get("file_name", "unknown")
                if fn not in files:
                    files[fn] = {
                        "file_name": fn,
                        "chunk_count": 0,
                        "chapters": [],
                    }
                files[fn]["chunk_count"] += 1
                if meta.get("chapter"):
                    ch = meta["chapter"]
                    if ch not in files[fn]["chapters"]:
                        files[fn]["chapters"].append(ch)

            result[coll] = {
                "count": store.count(coll),
                "files": sorted(files.values(), key=lambda x: x["file_name"]),
            }

        return JSONResponse(result)
    except Exception as e:
        logger.exception(f"获取文档列表失败: {e}")
        return JSONResponse(
            {"error": f"获取文档列表失败: {str(e)}"},
            status_code=500,
        )


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    collection: str = Form(""),
    password: str = Form(""),
    user_id: str = Form(""),
    department: str = Form("public"),
):
    """上传文件，保存到 uploads 目录并标记待处理

    支持格式: .pdf, .xlsx, .xls
    仅保存文件 + 记录到 sync_tracker（状态=pending），
    不自动处理。管理员需到「同步管理」Tab 选库后手动触发入库。

    权限：
      - 有 ADMIN_PASSWORD → 需提供正确的 password
      - 无 ADMIN_PASSWORD → 检查 user_id 是否有 upload 权限
      - 两者均未提供 → 拒绝
    """
    if not file.filename:
        raise HTTPException(400, "未提供文件")

    # 权限检查
    admin_pw = _get_admin_password()
    has_password = bool(admin_pw)
    if has_password:
        if password != admin_pw:
            raise HTTPException(403, "密码错误，无权上传")
    elif user_id:
        if not _check_user_permission(user_id, "upload"):
            raise HTTPException(403, "该用户无上传权限")
    else:
        raise HTTPException(403, "未授权，请提供 password 或 user_id")

    # 文件名净化（去路径、去危险字符、限长）
    original_name = _sanitize_filename(file.filename)
    ext = os.path.splitext(original_name)[1].lower()
    if ext not in _ALLOWED_EXT:
        raise HTTPException(400, f"不支持的文件类型: {ext or '(无扩展名)'}（仅支持 PDF/Excel/Markdown）")

    # 读取内容 + 大小限制
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"文件过大：{len(content) / 1024 / 1024:.1f} MB 超过上限 {MAX_UPLOAD_MB} MB")

    # MIME 内容签名双重校验（防伪造扩展名）
    if not _check_file_signature(original_name, content):
        raise HTTPException(400, f"文件内容与扩展名不符（伪造 {ext}？），已拒绝")

    # collection 白名单（防任意字符串注入路径）
    if collection and collection not in _ALLOWED_COLLECTIONS:
        raise HTTPException(400, f"未知的 collection: {collection}")
    _collection = collection or "standards"
    target_dir = FILE_DIRS[_collection]
    os.makedirs(target_dir, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex}_{original_name}"
    save_path = os.path.join(target_dir, safe_name)

    with open(save_path, "wb") as f:
        f.write(content)

    # 记录到 sync_tracker（标记 pending，等待管理员手动同步）
    _filename = original_name
    try:
        from .sync_tracker import SyncTracker
        tracker = SyncTracker()
        tracker.upsert_file(
            file_path=save_path,
            file_name=_filename,
            file_size=len(content),
            file_hash=file_sha256(save_path),
            target_collection=_collection,
            upload_user_id=user_id or "admin",
            upload_user_name="管理员",
            suggested_department=department,
        )
        tracker.close()
    except Exception as track_err:
        logger.warning(f"记录同步追踪失败（不影响文件保存）: {track_err}")

    logger.info(f"📤 文件已上传，标记待处理: {_filename} → {_collection}")

    return JSONResponse({
        "file_name": _filename,
        "collection": _collection,
        "message": "文件已上传，请到「同步管理」Tab 选库后手动同步",
    })


@router.get("/upload-status/{task_id}")
def upload_status(task_id: str,
                  password: str = Query("", description="管理员密码")):
    """查询异步上传任务的处理状态

    前端轮询此接口获取进度，status 取值:
      - pending:   排队等待处理
      - processing: 正在处理（提取→切块→入库）
      - done:       处理完成，result 中包含完整结果
      - error:      处理失败，error 中为错误信息
    """
    manager = get_task_manager()
    status = manager.get_status(task_id)
    if not status:
        raise HTTPException(404, f"任务不存在或已过期: {task_id}")
    return JSONResponse(status)


@router.delete("/docs")
def delete_docs(
    collection: str = Query(..., description="collection 名"),
    file_name: str = Query("", description="要删除的文件名（空则清空整个 collection）"),
    password: str = Query("", description="管理员密码"),
    user_id: str = Query("", description="用户 ID（钉钉端使用）"),
):
    """删除文档

    - 指定 file_name：删除该文件的所有切块
    - 不指定 file_name：清空整个 collection

    权限：
      - 有 ADMIN_PASSWORD → 需提供正确的 password
      - 无 ADMIN_PASSWORD → 检查 user_id 是否有 delete 权限
      - 两者均未提供 → 拒绝
    """
    # 权限检查
    admin_pw = _get_admin_password()
    has_password = bool(admin_pw)
    if has_password:
        if password != admin_pw:
            raise HTTPException(403, "密码错误，无权删除")
    elif user_id:
        if not _check_user_permission(user_id, "delete"):
            raise HTTPException(403, "该用户无删除权限")
    else:
        raise HTTPException(403, "未授权，请提供 password 或 user_id")
    store = get_store()

    if file_name:
        data = store.get(collection, where={"file_name": file_name})
        if data and data.get("ids"):
            deleted = store.delete(collection, ids=data["ids"])
            from scripts.skills.enhanced_search import invalidate_bm25_cache
            invalidate_bm25_cache(collection)
            return JSONResponse({
                "deleted": deleted,
                "collection": collection,
                "file_name": file_name,
            })
        return JSONResponse({
            "deleted": 0,
            "collection": collection,
            "file_name": file_name,
            "message": "未找到该文档",
        })

    # 清空整个 collection
    data = store.get(collection)
    if data and data.get("ids"):
        deleted = store.delete(collection, ids=data["ids"])
        from scripts.skills.enhanced_search import invalidate_bm25_cache
        invalidate_bm25_cache(collection)
        return JSONResponse({"deleted": deleted, "collection": collection})
    return JSONResponse({"deleted": 0, "collection": collection})


@router.get("/search")
def search_docs(
    q: str = Query(..., description="搜索关键词"),
    collection: str = Query("", description="要搜索的 collection（空则搜所有）"),
    limit: int = Query(5, ge=1, le=20, description="返回条数"),
    password: str = Query("", description="管理员密码"),
):
    """在线搜索测试

    直接查询向量数据库，返回匹配结果及距离分数。
    """
    if not _verify_admin_access(password):
        raise HTTPException(403, "密码错误，无权访问")
    store = get_store()

    if collection:
        results = store.query(collection, q, n_results=limit)
        return JSONResponse({
            "collection": collection,
            "results": _format_results(results),
        })

    # 搜所有 collection
    collections = store.list_collections()
    all_results: dict = {}
    for coll in collections:
        results = store.query(coll, q, n_results=limit)
        all_results[coll] = _format_results(results)
    return JSONResponse({"results": all_results})


@router.get("/stats", response_class=HTMLResponse)
def stats_page(request: Request):
    """使用统计看板页面"""
    password = _check_password(request)
    if not _verify_admin_access(password):
        return HTMLResponse(
            content="<h2>需要密码</h2>"
                     "<form method='get'>"
                     "密码：<input name='password' type='password'>"
                     "<input type='submit' value='进入'>"
                     "</form>"
                     "<p>（请联系管理员获取密码）</p>",
            status_code=401,
        )

    # 获取统计数据
    try:
        from scripts.user_store import get_store
        store = get_store()
        stats = store.get_stats()
        users = store.list_users()
        per_user = store.get_all_users_memory_count()
    except Exception as e:
        logger.exception(f"获取统计数据失败: {e}")
        stats = {"total_users": 0, "active_today": 0, "total_messages": 0,
                 "daily_trend": [], "top_users": []}
        users = []
        per_user = {}

    # 渲染 HTML
    html = _build_stats_html(stats, users, per_user, password)
    return HTMLResponse(content=html)


def _build_stats_html(stats: dict, users: list[dict],
                       per_user: dict[str, int], password: str) -> str:
    """构建统计看板 HTML"""
    pw_param = f"?password={password}" if password else ""

    # 用户表格行（所有用户可控字段统一转义，防存储型 XSS）
    user_rows = ""
    for u in users:
        uid = html.escape(u.get("user_id", "")[:24])
        nick = html.escape(u.get("nick", "") or "-")
        title = html.escape(u.get("title", "") or "-")
        leader = "✅ 主管" if u.get("leader") else ""
        try:
            dept = ", ".join(json.loads(u.get("department_names", "[]")))
        except (ValueError, TypeError):
            dept = ""
        dept = html.escape(dept or "-")
        msg_count = per_user.get(u.get("user_id", ""), 0)
        last_active = html.escape((u.get("last_active", "") or "")[:10])
        user_id_attr = html.escape(u.get("user_id", ""))
        user_rows += (
            f"<tr><td title='{user_id_attr}'>{uid}</td>"
            f"<td>{nick}</td>"
            f"<td>{title}</td>"
            f"<td>{leader}</td>"
            f"<td>{dept}</td>"
            f"<td>{msg_count}</td>"
            f"<td>{last_active}</td></tr>\n"
        )

    # 每日趋势
    trend_bars = ""
    max_count = max((d["count"] for d in stats["daily_trend"]), default=1)
    for d in stats["daily_trend"]:
        pct = (d["count"] / max_count * 80) if max_count else 0
        trend_bars += (
            f"<div class='trend-row'>"
            f"<span class='trend-date'>{html.escape(d['date'][5:])}</span>"
            f"<span class='trend-bar' style='width:{pct}%'>{d['count']}</span>"
            f"</div>\n"
        )

    # Top 用户
    top_rows = ""
    for i, u in enumerate(stats["top_users"][:10], 1):
        uid = html.escape(u.get("user_id", ""))
        top_rows += (
            f"<tr><td>{i}</td>"
            f"<td title='{uid}'>{uid[:24]}</td>"
            f"<td>{u.get('count', 0)}</td></tr>\n"
        )

    return f"""<!DOCTYPE html>
<html lang='zh-CN' data-theme='light'>
<head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1.0'>
<title>恩特小助手 - 使用统计</title>
<style>
:root {{
  --brand: #4361ee; --pink: #f72585;
  --bg: #f8fafc; --card: #ffffff; --text: #1a1a2e;
  --text-secondary: #64748b; --border: #eef2f6;
  --shadow: 0 2px 8px rgba(0,0,0,0.05);
  --shadow-lg: 0 8px 40px rgba(0,0,0,0.10);
  --hover: #f1f5f9;
}}
[data-theme='dark'] {{
  --brand: #6c8cff; --bg: #0f172a; --card: #1e293b;
  --text: #e2e8f0; --text-secondary: #94a3b8; --border: #334155;
  --shadow: 0 2px 8px rgba(0,0,0,0.3);
  --shadow-lg: 0 8px 40px rgba(0,0,0,0.45);
  --hover: #334155;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;
       background: var(--bg); color: var(--text); min-height: 100vh;
       transition: background .3s, color .3s; }}
.page {{ max-width: 1000px; margin: 0 auto; padding: 24px 28px; }}
.topbar {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }}
.brand {{ display: flex; align-items: center; gap: 10px; }}
.logo {{ width: 38px; height: 38px; border-radius: 11px;
         background: linear-gradient(135deg, #4361ee, #f72585);
         display: flex; align-items: center; justify-content: center; font-size: 19px;
         box-shadow: 0 4px 12px rgba(67,97,238,.25); }}
.brand h1 {{ font-size: 19px; font-weight: 800; letter-spacing: .3px;
            background: linear-gradient(135deg, var(--brand), var(--pink));
            -webkit-background-clip: text; background-clip: text;
            -webkit-text-fill-color: transparent; }}
.nav {{ display: flex; align-items: center; gap: 8px; }}
.nav a, .nav button {{
  color: var(--text-secondary); text-decoration: none; font-size: 13px;
  padding: 7px 14px; border-radius: 9px; background: var(--card);
  border: 1px solid var(--border); transition: all .2s; cursor: pointer;
  font-family: inherit;
}}
.nav a:hover, .nav button:hover {{ color: var(--text); border-color: var(--brand); }}
.nav a.primary {{ background: var(--brand); color: #fff; border-color: var(--brand); }}
.nav a.primary:hover {{ background: #3a56d4; }}
.card {{ background: var(--card); border-radius: 16px; padding: 24px;
        box-shadow: var(--shadow); border: 1px solid var(--border);
        margin-bottom: 16px; transition: background .3s; }}
.card h2 {{ font-size: 15px; font-weight: 700; margin-bottom: 14px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; }}
.stat-box {{ text-align: center; padding: 22px 16px; }}
.stat-num {{ font-size: 34px; font-weight: 800;
            background: linear-gradient(135deg, var(--brand), var(--pink));
            -webkit-background-clip: text; background-clip: text;
            -webkit-text-fill-color: transparent; }}
.stat-label {{ font-size: 13px; color: var(--text-secondary); margin-top: 6px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th, td {{ padding: 9px 12px; text-align: left; border-bottom: 1px solid var(--border); }}
th {{ color: var(--text-secondary); font-weight: 600; background: var(--hover); }}
tr:hover td {{ background: var(--hover); }}
.trend-row {{ margin: 5px 0; }}
.trend-date {{ display: inline-block; width: 80px; font-size: 12px; color: var(--text-secondary); }}
.trend-bar {{ display: inline-block; height: 20px; border-radius: 5px;
              background: linear-gradient(90deg, var(--brand), #5a7bff);
              text-align: right; color: #fff; font-size: 11px;
              padding-right: 6px; line-height: 20px; min-width: 2px; }}
.foot {{ color: var(--text-secondary); font-size: 12px; margin-top: 20px; }}
@media (max-width: 768px) {{ .page {{ padding: 16px; }} }}
</style></head>
<body>
<div class='page'>
  <div class='topbar'>
    <div class='brand'>
      <div class='logo'>🤖</div>
      <h1>恩特小助手 · 使用统计</h1>
    </div>
    <div class='nav'>
      <a href='/admin{pw_param}' class='primary'>← 返回管理</a>
      <button class='theme-btn' onclick='toggleTheme(this)'>🌙</button>
    </div>
  </div>

  <div class='grid'>
    <div class='card stat-box'><div class='stat-num'>{stats['total_users']}</div><div class='stat-label'>累计用户</div></div>
    <div class='card stat-box'><div class='stat-num'>{stats['active_today']}</div><div class='stat-label'>今日活跃</div></div>
    <div class='card stat-box'><div class='stat-num'>{stats['total_messages']}</div><div class='stat-label'>累计消息数</div></div>
    <div class='card stat-box'><div class='stat-num'>{len(users)}</div><div class='stat-label'>注册用户</div></div>
  </div>

  <div class='card'>
    <h2>📈 每日消息趋势（近 7 天）</h2>
    {'<p style="color:var(--text-secondary)">暂无数据</p>' if not stats['daily_trend'] else trend_bars}
  </div>

  <div class='card'>
    <h2>👤 对话量 Top 10</h2>
    <table>
      <tr><th>#</th><th>用户</th><th>消息数</th></tr>
      {'<tr><td colspan="3" style="color:var(--text-secondary)">暂无数据</td></tr>' if not stats['top_users'] else top_rows}
    </table>
  </div>

  <div class='card'>
    <h2>👥 所有用户</h2>
    <table>
      <tr><th>用户ID</th><th>昵称</th><th>职位</th><th>身份</th>
        <th>部门</th><th>消息数</th><th>最后活跃</th></tr>
      {user_rows if user_rows else '<tr><td colspan="7" style="color:var(--text-secondary)">暂无用户</td></tr>'}
    </table>
  </div>

  <p class='foot'>数据来源：user_store.db SQLite · 更新时间：{stats.get('_generated_at', '')}</p>
</div>
<script>
function toggleTheme(btn) {{
  var h = document.documentElement;
  var cur = h.getAttribute('data-theme') || 'light';
  var next = cur === 'dark' ? 'light' : 'dark';
  h.setAttribute('data-theme', next);
  localStorage.setItem('stats_theme', next);
  btn.textContent = next === 'dark' ? '☀️' : '🌙';
}}
(function() {{
  var saved = localStorage.getItem('stats_theme') || 'light';
  document.documentElement.setAttribute('data-theme', saved);
  var b = document.querySelector('.theme-btn');
  if (b) b.textContent = saved === 'dark' ? '☀️' : '🌙';
}})();
</script>
</body></html>"""


def _format_results(results: dict) -> list[dict]:
    """将 Chroma query 结果格式化为 JSON"""
    if not results or not results.get("documents") or not results["documents"][0]:
        return []
    items = []
    for i in range(len(results["documents"][0])):
        item = {
            "text": results["documents"][0][i][:500],
            "metadata": results["metadatas"][0][i] if results.get("metadatas") else {},
        }
        if results.get("distances") and results["distances"][0]:
            dist = results["distances"][0][i]
            item["score"] = round(float(dist), 4)
            # 余弦距离转相似度（便于展示）
            item["similarity"] = round(max(0, 1 - float(dist)), 4)
        items.append(item)
    return items


# ==================== v1.2.5 同步管理 API ====================


@router.get("/sync-files")
def list_sync_files(password: str = Query("", description="管理员密码")):
    """列出 data/uploads/ 和 data/standards/ 中所有可同步文件及状态"""
    if not _verify_admin_access(password):
        raise HTTPException(401, "密码错误")

    from .sync_tracker import SyncTracker
    tracker = SyncTracker()

    # 获取已追踪文件的状态
    tracked = {s["file_path"]: s for s in tracker.list_all()}

    # 扫描目录
    scan_dirs = {
        "uploads":     (UPLOAD_DIR, "standards"),
        "standards":   (str(STANDARDS_DIR), "standards"),
        "fault_codes": (str(FAULT_CODES_DIR), "error_codes"),
        "experience":  (str(EXPERIENCE_DIR), "experience_kb"),
    }
    result: dict = {"directories": {}}

    for dir_key, (abs_dir, default_coll) in scan_dirs.items():
        abs_dir = os.path.normpath(abs_dir)
        files: list[dict] = []
        if os.path.isdir(abs_dir):
            for root, dirs, fnames in os.walk(abs_dir):
                if "mineru_output" in root:
                    continue
                for fn in sorted(fnames):
                    ext = os.path.splitext(fn)[1].lower()
                    if ext not in (".pdf", ".xlsx", ".xls", ".md"):
                        continue
                    fp = os.path.join(root, fn)
                    try:
                        st = os.stat(fp)
                    except OSError:
                        continue
                    status = tracked.get(fp, {})
                    upload_user_name = status.get("upload_user_name", "")
                    # 如果目录名包含用户信息（格式: 用户名_ID），提取作为备选
                    if not upload_user_name:
                        dir_parts = os.path.basename(os.path.dirname(fp)).split("_", 1)
                        if len(dir_parts) > 1:
                            upload_user_name = dir_parts[0]
                    files.append({
                        "file_path": fp,
                        "file_name": fn,
                        "file_size": st.st_size,
                        "modified": dt.fromtimestamp(st.st_mtime).isoformat(),
                        "sync_status": status.get("sync_status", "new"),
                        "target_collection": status.get("target_collection", default_coll),
                        "suggested_department": status.get("suggested_department") or "public",
                        "upload_user_id": status.get("upload_user_id", ""),
                        "upload_user_name": upload_user_name,
                        "error_message": status.get("error_message", ""),
                        "last_synced_at": status.get("last_synced_at", ""),
                    })
        result["directories"][dir_key] = {
            "path": rel_dir,
            "default_collection": default_coll,
            "files": files,
        }

    return JSONResponse(result)


@router.post("/sync-trigger")
def trigger_sync(
    file_path: str = Form(""),
    collection: str = Form(""),
    department: str = Form(""),
    sync_all: bool = Form(False),
    force: bool = Form(False),
    password: str = Form(""),
):
    """手动触发文件同步

    - file_path 指定单个文件
    - collection 目标库；department 所属中心（空串 = 保持现有/默认 public）
    - sync_all=True 同步所有待处理/失败文件
    - force=True 跳过 Chroma 预检查，强制重新入库
    """
    if not _verify_admin_access(password):
        raise HTTPException(403, "密码错误，无权操作")

    from .engine import process_file
    from .sync_tracker import SyncTracker
    tracker = SyncTracker()

    if file_path:
        # 同步单个文件 — 异步后台处理，不阻塞 HTTP
        if not _is_within_data_dir(file_path):
            raise HTTPException(400, "只能同步 data/ 目录内的文件")
        if not os.path.isfile(file_path):
            raise HTTPException(400, f"文件不存在: {file_path}")

        fname = os.path.basename(file_path)
        target = collection or "standards"

        # 提交后台任务；新版本完整写入后由存储层替换旧版本
        from .task_manager import get_manager as get_task_manager
        manager = get_task_manager()
        record = manager.submit(
            file_name=fname,
            collection=target,
            process_fn=lambda: _run_sync_task(
                file_path, fname, target, force, department,
            ),
        )

        logger.info(f"🔄 同步任务已提交: {fname} → task_id={record.task_id}")

        return JSONResponse({
            "task_id": record.task_id,
            "status": "processing",
            "file_name": fname,
            "message": "同步任务已提交，后台处理中",
        })

    if sync_all:
        try:
            from .scheduler import trigger_manual_sync
            stats = trigger_manual_sync(force=force)
            return JSONResponse({"status": "done", "stats": stats})
        except Exception as e:
            raise HTTPException(500, f"批量同步失败: {e}")

    raise HTTPException(400, "需指定 file_path 或 sync_all=True")


def _run_sync_task(file_path: str, fname: str,
                   target: str, force: bool,
                   department: str = "") -> object:
    """后台执行同步任务（被 task_manager 调用）

    独立的模块级函数，在后台线程中执行，自动关联进度上报。
    归属优先级：显式传入 department（改归属）> 从 sync_tracker 读取。
    """
    from .engine import process_file
    from .sync_tracker import SyncTracker
    tracker = SyncTracker()

    try:
        fsize = os.path.getsize(file_path)
        fhash = file_sha256(file_path)
    except OSError:
        fsize, fhash = 0, ""

    tracker.upsert_file(file_path, fname, fsize, fhash, target)

    from .task_manager import report_progress as _rp
    _rp("syncing", 10, "开始同步处理...")

    # 归属：显式传入优先（先落库到 tracker 保持一致）；否则从 tracker 读取
    if department:
        tracker.update_department(file_path, department)
    else:
        status = tracker.get_status(file_path)
        department = (status.get("suggested_department", "public")
                      if status else "public")

    doc = process_file(file_path, file_name=fname,
                       target_collection=target, force=force,
                       department=department)

    if doc.status in ("done", "skipped"):
        tracker.mark_synced(file_path)
    else:
        tracker.mark_error(file_path, doc.message or "未知状态")

    return doc


@router.get("/recent-tasks")
def recent_tasks(password: str = Query("", description="管理员密码")):
    """获取最近的后台任务列表（用于前端进度追踪面板）"""
    if not _verify_admin_access(password):
        raise HTTPException(401, "密码错误")

    from .task_manager import get_manager as get_task_manager
    manager = get_task_manager()
    tasks = manager.get_recent_tasks(limit=20)

    # 补充 sync_tracker 中的同步记录
    from .sync_tracker import SyncTracker
    tracker = SyncTracker()
    history = tracker.get_history(limit=10)

    return JSONResponse({
        "running_tasks": tasks,
        "sync_history": history,
    })


@router.post("/sync-delete")
def sync_delete_file(
    file_path: str = Form(...),
    password: str = Form(""),
):
    """删除同步管理中的源文件（磁盘文件 + sync_tracker 记录）

    注意：只删除磁盘上的源文件，不影响已入库的 Chroma 知识库。
    如需删除知识库内容，请到「文档列表」Tab 操作。
    """
    if not _verify_admin_access(password):
        raise HTTPException(403, "密码错误，无权操作")

    if not file_path:
        raise HTTPException(400, "缺少 file_path")

    # 路径白名单：只能删除 data/ 目录内的源文件
    if not _is_within_data_dir(file_path):
        raise HTTPException(400, "只能删除 data/ 目录内的源文件")

    if not os.path.isfile(file_path):
        raise HTTPException(404, f"文件不存在: {file_path}")

    fname = os.path.basename(file_path)
    try:
        # 1. 删除磁盘文件
        os.remove(file_path)
        logger.info(f"已删除源文件: {file_path}")

        # 2. 删除 sync_tracker 记录
        from .sync_tracker import SyncTracker
        tracker = SyncTracker()
        # SQLite 直接 DELETE 该文件记录
        import sqlite3
        conn = tracker._get_conn()
        conn.execute("DELETE FROM sync_status WHERE file_path = ?", (file_path,))
        conn.commit()

        return JSONResponse({
            "status": "deleted",
            "file_name": fname,
            "message": f"已删除: {fname}",
        })
    except Exception as e:
        logger.exception(f"删除文件失败: {file_path}")
        raise HTTPException(500, f"删除失败: {e}")


@router.get("/sync-status")
def sync_status(
    status_filter: str = Query("", alias="status",
                                description="过滤: pending/synced/error"),
    collection_filter: str = Query("", alias="collection",
                                    description="按 collection 过滤"),
    limit: int = Query(100, ge=1, le=500),
    password: str = Query("", description="管理员密码"),
):
    """查询同步历史记录"""
    if not _verify_admin_access(password):
        raise HTTPException(401, "密码错误")

    from .sync_tracker import SyncTracker
    tracker = SyncTracker()
    records = tracker.list_all(
        status_filter=status_filter,
        collection_filter=collection_filter,
    )
    records = sorted(
        records,
        key=lambda r: r.get("updated_at", ""),
        reverse=True,
    )[:limit]
    return JSONResponse({"records": records, "total": len(records)})


@router.get("/users")
def list_admin_users(password: str = Query("", description="管理员密码")):
    """列出所有用户（含归属中心信息）"""
    if not _verify_admin_access(password):
        raise HTTPException(401, "密码错误")
    try:
        from scripts.user_store import get_store as get_user_store
        store = get_user_store()
        users = store.list_users()
        # 脱敏处理
        result = []
        for u in users:
            result.append({
                "user_id": u.get("user_id", ""),
                "nick": u.get("nick", "") or u.get("user_id", "")[:16],
                "staff_id": u.get("staff_id", ""),
                "role": u.get("role", "user"),
                "leader": bool(u.get("leader", 0)),
                "center": u.get("center", "public"),
                "centers": json.loads(u.get("centers", "[]")) if isinstance(u.get("centers"), str) else [],
                "last_active": u.get("last_active", ""),
                "created_at": u.get("created_at", ""),
            })
        return JSONResponse({"users": result, "total": len(result)})
    except Exception as e:
        raise HTTPException(500, f"查询用户失败: {e}")


@router.post("/users/update-centers")
def update_user_centers(
    user_id: str = Form(...),
    centers: str = Form("[]"),
    password: str = Form(""),
):
    """更新用户的归属中心列表"""
    if not _verify_admin_access(password):
        raise HTTPException(403, "密码错误")
    try:
        from scripts.user_store import get_store as get_user_store
        centers_list = json.loads(centers)
        if not isinstance(centers_list, list):
            raise HTTPException(400, "centers 必须是 JSON 数组")
        ok = get_user_store().set_user_centers(user_id, centers_list)
        if ok:
            return JSONResponse({"status": "ok", "user_id": user_id, "centers": centers_list})
        return JSONResponse({"status": "error", "message": "更新失败"})
    except json.JSONDecodeError:
        raise HTTPException(400, "centers 格式错误，需为 JSON 数组")
    except Exception as e:
        raise HTTPException(500, f"更新用户中心失败: {e}")


@router.get("/sync-stats")
def sync_stats(password: str = Query("", description="管理员密码")):
    """同步统计概览"""
    if not _verify_admin_access(password):
        raise HTTPException(401, "密码错误")

    from .sync_tracker import SyncTracker
    tracker = SyncTracker()
    stats = tracker.get_stats()

    # 补充目录文件数
    for dir_key, abs_dir in [("files_in_uploads", UPLOAD_DIR),
                              ("files_in_standards", str(STANDARDS_DIR)),
                              ("files_in_fault_codes", str(FAULT_CODES_DIR)),
                              ("files_in_experience", str(EXPERIENCE_DIR))]:
        abs_dir = os.path.normpath(abs_dir)
        count = 0
        if os.path.isdir(abs_dir):
            for root, dirs, fnames in os.walk(abs_dir):
                if "mineru_output" in root:
                    continue
                for fn in fnames:
                    if os.path.splitext(fn)[1].lower() in (".pdf", ".xlsx", ".xls", ".md"):
                        count += 1
        stats[dir_key] = count

    return JSONResponse(stats)


# ===== 按人查看（v1.12.8：Web 端只做管理层，看每人上传的内容与学习进度） =====


def _aggregate_files_by_user(records: list[dict]) -> list[dict]:
    """将 sync_tracker 记录按上传者（upload_user_id）聚合成「按人查看」数据。

    返回每人一条：文件数、各学习状态统计（synced/pending/error）、文件明细，
    按最近更新倒序；upload_user_id 为空的记录归入「未记录上传者」组并排最后
    （管理员直传/旧数据无归属）。

    同一人多条记录可能昵称只有部分非空（老数据/补录），取最近一条非空昵称。
    """
    groups: dict[str, dict] = {}
    for r in records:
        uid = (r.get("upload_user_id") or "").strip()
        if uid not in groups:
            groups[uid] = {
                "upload_user_id": uid,
                "upload_user_name": "",
                "files": [],
                "synced": 0,
                "pending": 0,
                "error": 0,
                "last_updated": "",
            }
        g = groups[uid]
        name = (r.get("upload_user_name") or "").strip()
        if name:
            g["upload_user_name"] = name
        g["files"].append({
            "file_name": r.get("file_name", ""),
            "file_path": r.get("file_path", ""),
            "target_collection": r.get("target_collection", ""),
            "sync_status": r.get("sync_status", ""),
            "error_message": r.get("error_message", "") or "",
            "last_synced_at": r.get("last_synced_at", "") or "",
            "file_size": r.get("file_size", 0),
        })
        st = r.get("sync_status", "")
        if st in ("synced", "pending", "error"):
            g[st] += 1
        up = r.get("updated_at") or ""
        if up > g["last_updated"]:
            g["last_updated"] = up
    # 非空 id 在前（按最近更新倒序），空 id（未记录上传者）沉底
    return sorted(groups.values(),
                  key=lambda g: (g["upload_user_id"] != "",
                                 g["last_updated"] or ""),
                  reverse=True)


@router.get("/people")
def list_people(password: str = Query("", description="管理员密码")):
    """按人查看：每人上传的文件与学习进度（管理层视图）

    数据来自 sync_status 表（上传时记录的 upload_user_id/upload_user_name），
    聚合由 _aggregate_files_by_user 完成，前端「按人查看」tab 消费。
    """
    if not _verify_admin_access(password):
        raise HTTPException(401, "密码错误")

    from .sync_tracker import SyncTracker
    tracker = SyncTracker()
    people = _aggregate_files_by_user(tracker.list_all())
    return JSONResponse({"people": people, "total": len(people)})


@router.get("/dashboard-status")
def dashboard_status(password: str = Query("", description="管理员密码")):
    """看板任务最近一次实际运行状态。

    不把“订阅存在”伪装成“今天已经成功推送”：页面需要展示任务是否真的运行，
    失败发生在哪一段，以及失败提醒是否已送达。
    """
    if not _verify_admin_access(password):
        raise HTTPException(401, "密码错误")
    from scripts.dashboard.subscription_store import get_subscription_store
    items = []
    for sub in get_subscription_store().list_all():
        items.append({
            "id": sub.id,
            "title": sub.title,
            "owner_user_id": sub.owner_user_id,
            "enabled": sub.enabled,
            "schedule": f"{int(sub.push_hour):02d}:{int(sub.push_minute):02d}",
            "last_pushed_at": sub.last_pushed_at,
            "last_run_at": sub.last_run_at,
            "last_run_status": sub.last_run_status,
            "last_run_stage": sub.last_run_stage,
            "last_run_reason": sub.last_run_reason,
            "last_alert_status": sub.last_alert_status,
        })
    return JSONResponse({"subscriptions": items, "total": len(items)})


# ===== 反馈统计 =====

@router.get("/feedback-stats")
def feedback_stats(password: str = Query("", description="管理员密码")):
    """回答反馈统计"""
    if not _verify_admin_access(password):
        raise HTTPException(401, "密码错误")

    from scripts.user_store import get_feedback_stats
    stats = get_feedback_stats()
    return JSONResponse(stats)


# ===== Prompt 管理 =====

@router.get("/prompts", response_class=HTMLResponse)
def prompts_page(password: str = Query("", description="管理员密码")):
    """Prompt 编辑页面"""
    if not _verify_admin_access(password):
        raise HTTPException(401, "密码错误")

    from scripts.user_store import get_prompt, list_prompts
    import sys
    prompt_file = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "prompts", "system_prompt.txt"
    )
    # 优先从 DB 读取
    content = get_prompt("system")
    if content is None:
        try:
            with open(prompt_file, "r", encoding="utf-8") as f:
                content = f.read()
        except FileNotFoundError:
            content = ""

    prompt_names = list_prompts()

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Prompt 管理 — 恩特小助手</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px; background: #f5f5f5; }}
h1 {{ color: #333; }}
.info {{ color: #666; font-size: 14px; margin-bottom: 20px; }}
textarea {{ width: 100%; height: 500px; font-family: 'Consolas', monospace; font-size: 14px; padding: 12px; border: 1px solid #ddd; border-radius: 8px; resize: vertical; box-sizing: border-box; }}
.btns {{ margin-top: 16px; display: flex; gap: 12px; }}
.btn {{ padding: 10px 24px; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: 600; }}
.btn-primary {{ background: #4361ee; color: #fff; }}
.btn-primary:hover {{ background: #3651d4; }}
.btn-danger {{ background: #ef4444; color: #fff; }}
.btn-danger:hover {{ background: #dc2626; }}
.toast {{ position: fixed; top: 20px; right: 20px; padding: 12px 24px; border-radius: 8px; color: #fff; font-weight: 600; z-index: 9999; display: none; }}
.toast.success {{ background: #22c55e; }}
.toast.error {{ background: #ef4444; }}
</style></head><body>
<h1>📝 Prompt 管理</h1>
<p class="info">编辑系统提示词，保存后立即生效（无需重启）。已保存的 Prompt：{', '.join(prompt_names) or '（无，使用文件默认）'}</p>
<form id="promptForm">
<textarea id="promptContent" name="content">{html.escape(content)}</textarea>
<div class="btns">
<button type="submit" class="btn btn-primary">💾 保存并生效</button>
<button type="button" class="btn btn-danger" onclick="resetPrompt()">🔄 重置为文件默认</button>
</div>
</form>
<div id="toast" class="toast"></div>
<script>
function showToast(text, type) {{
    const t = document.getElementById('toast');
    t.textContent = text;
    t.className = 'toast ' + type;
    t.style.display = 'block';
    setTimeout(() => t.style.display = 'none', 3000);
}}
document.getElementById('promptForm').addEventListener('submit', async (e) => {{
    e.preventDefault();
    const content = document.getElementById('promptContent').value;
    const fd = new FormData();
    fd.append('content', content);
    fd.append('password', new URLSearchParams(location.search).get('password') || '');
    const r = await fetch('/admin/prompts/system', {{ method: 'POST', body: fd }});
    const data = await r.json();
    showToast(data.ok ? '保存成功！' : '保存失败: ' + (data.error||''), data.ok ? 'success' : 'error');
}});
async function resetPrompt() {{
    if (!confirm('确定要重置为文件默认值？当前编辑内容将丢失。')) return;
    const fd = new FormData();
    fd.append('password', new URLSearchParams(location.search).get('password') || '');
    const r = await fetch('/admin/prompts/reset', {{ method: 'POST', body: fd }});
    const data = await r.json();
    if (data.ok) {{ document.getElementById('promptContent').value = data.content; showToast('已重置', 'success'); }}
    else showToast('重置失败', 'error');
}}
</script></body></html>"""


@router.post("/prompts/system")
async def save_system_prompt(
    content: str = Form(...),
    password: str = Form("", description="管理员密码"),
):
    """保存 system prompt 到 DB 并清除缓存"""
    if not _verify_admin_access(password):
        raise HTTPException(401, "密码错误")

    from scripts.user_store import set_prompt
    set_prompt("system", content)

    # 清除 agent.py 的 prompt 缓存
    try:
        from scripts.skills.agent import reload_system_prompt
        reload_system_prompt()
    except Exception as e:
        logger.warning(f"清除 prompt 缓存失败: {e}")

    logger.info("System prompt 已更新")
    return JSONResponse({"ok": True})


@router.post("/prompts/reset")
async def reset_system_prompt(password: str = Form("", description="管理员密码")):
    """重置 system prompt 为文件默认值"""
    if not _verify_admin_access(password):
        raise HTTPException(401, "密码错误")

    prompt_file = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "prompts", "system_prompt.txt"
    )
    try:
        with open(prompt_file, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return JSONResponse({"error": "默认 prompt 文件不存在"}, status_code=404)

    from scripts.user_store import set_prompt
    set_prompt("system", content)

    try:
        from scripts.skills.agent import reload_system_prompt
        reload_system_prompt()
    except Exception as e:
        logger.warning(f"清除 prompt 缓存失败: {e}")

    logger.info("System prompt 已重置为文件默认值")
    return JSONResponse({"ok": True, "content": content})


# ===== 系统设置（配置覆盖，v1.14.0） =====

@router.get("/config")
def get_admin_config(password: str = Query("", description="管理员密码")):
    """系统设置页数据：各配置项掩码状态，由 /admin「系统设置」tab fetch 渲染。"""
    if not _verify_admin_access(password):
        raise HTTPException(401, "密码错误")
    from scripts.admin_config import get_status
    return get_status()


@router.post("/config")
async def save_admin_config(request: Request, password: str = Form("", description="管理员密码")):
    """保存系统设置：白名单内 key 写入 data/admin_config.json（原子写）。

    - 留空 = 不修改该项；未知 key 静默忽略
    - ADMIN_PASSWORD 保存后立即生效（setattr 热更新，鉴权每次现读）
    - 其余密钥需重启服务生效（各模块 import 快照）
    """
    if not _verify_admin_access(password):
        raise HTTPException(401, "密码错误")
    form = await request.form()
    values = {k: str(v) for k, v in form.items() if k != "password"}
    from scripts.admin_config import save_config
    result = save_config(values)
    return JSONResponse({"ok": True, **result})
