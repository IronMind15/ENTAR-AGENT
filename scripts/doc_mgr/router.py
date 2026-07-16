"""
文档管理 API 路由

提供 REST API 供管理页面调用。
所有路由以 /admin 为前缀，由 main.py 挂载。

权限说明：
  - 未设 ADMIN_PASSWORD 时，/admin 页面、上传、删除均开放（向后兼容）
  - 设置 ADMIN_PASSWORD 后，需在请求中带 ?password=xxx 参数
  - 钉钉端上传由 user_store.check_permission() 控制（基于 leader/role）
"""

import json
import os
import uuid
import logging
from datetime import datetime as dt
from fastapi import APIRouter, UploadFile, File, Form, Query, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .engine import process_file
from .storage import get_store
from .views import ADMIN_HTML
from .task_manager import get_manager as get_task_manager

logger = logging.getLogger("doc_mgr.router")

router = APIRouter(prefix="/admin")

# 文件保存目录映射（按 collection 分类存储）
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", ".."))

FILE_DIRS = {
    "standards":  os.path.join(_PROJECT_ROOT, "data", "standards"),
    "error_codes": os.path.join(_PROJECT_ROOT, "data", "fault_codes"),
}

# 钉钉上传目录（按用户/日期分类）
UPLOAD_DIR = os.path.join(_PROJECT_ROOT, "data", "uploads")


def _get_admin_password() -> str:
    """读取配置的管理员密码（空表示不开启）"""
    try:
        import importlib
        cfg = importlib.import_module("config")
        return getattr(cfg, "ADMIN_PASSWORD", "") or ""
    except Exception:
        return ""


def _verify_admin_access(password: str = "") -> bool:
    """验证管理员访问权限"""
    admin_pw = _get_admin_password()
    if not admin_pw:
        return True  # 没设密码 = 开放
    return password == admin_pw


def _check_password(request: Request) -> str | None:
    """从请求参数中获取密码"""
    return request.query_params.get("password", "")


def _check_user_permission(user_id: str = "", action: str = "upload") -> bool:
    """检查用户是否有权限执行操作"""
    if not user_id:
        return False
    try:
        from user_store import get_store
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
                     "<form method='get'>"
                     "密码：<input name='password' type='password'>"
                     "<input type='submit' value='进入'>"
                     "</form>"
                     "<p>（请联系管理员获取密码）</p>",
            status_code=401,
        )
    return ADMIN_HTML


@router.get("/collections")
def list_collections():
    """列出所有 collection 及其统计数据"""
    store = get_store()
    collections = store.list_collections()
    result = {}
    for coll in collections:
        result[coll] = {"count": store.count(coll)}
    return JSONResponse(result)


@router.get("/docs")
def list_docs(collection: str = Query("", description="按 collection 过滤")):
    """列出已入库文档，按 collection 分组

    从 Chroma 中读取 metadata，按 file_name 字段归组。
    """
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

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in (".pdf", ".xlsx", ".xls"):
        raise HTTPException(400, f"不支持的文件类型: {ext}（仅支持 PDF/Excel）")

    # 按 collection 保存到对应目录（standards/ 或 fault_codes/）
    _collection = collection or "standards"
    target_dir = FILE_DIRS.get(_collection, UPLOAD_DIR)
    os.makedirs(target_dir, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex}_{file.filename}"
    save_path = os.path.join(target_dir, safe_name)

    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    # 记录到 sync_tracker（标记 pending，等待管理员手动同步）
    _filename = file.filename
    try:
        from .sync_tracker import SyncTracker
        tracker = SyncTracker()
        tracker.upsert_file(
            file_path=save_path,
            file_name=_filename,
            file_size=len(content),
            file_hash=str(int(os.path.getmtime(save_path))),
            target_collection=_collection,
            upload_user_id=user_id or "admin",
            upload_user_name="管理员",
        )
    except Exception as track_err:
        logger.warning(f"记录同步追踪失败（不影响文件保存）: {track_err}")

    logger.info(f"📤 文件已上传，标记待处理: {_filename} → {_collection}")

    return JSONResponse({
        "file_name": _filename,
        "collection": _collection,
        "message": "文件已上传，请到「同步管理」Tab 选库后手动同步",
    })


@router.get("/upload-status/{task_id}")
def upload_status(task_id: str):
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
        return JSONResponse({"deleted": deleted, "collection": collection})
    return JSONResponse({"deleted": 0, "collection": collection})


@router.get("/search")
def search_docs(
    q: str = Query(..., description="搜索关键词"),
    collection: str = Query("", description="要搜索的 collection（空则搜所有）"),
    limit: int = Query(5, ge=1, le=20, description="返回条数"),
):
    """在线搜索测试

    直接查询向量数据库，返回匹配结果及距离分数。
    """
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
        from user_store import get_store
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

    # 用户表格行
    user_rows = ""
    for u in users:
        uid = u.get("user_id", "")[:24]
        nick = u.get("nick", "") or "-"
        title = u.get("title", "") or "-"
        leader = "✅ 主管" if u.get("leader") else ""
        dept = ", ".join(json.loads(u.get("department_names", "[]"))) or "-"
        msg_count = per_user.get(u.get("user_id", ""), 0)
        last_active = (u.get("last_active", "") or "")[:10]
        user_rows += (
            f"<tr><td title='{u.get('user_id','')}'>{uid}</td>"
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
            f"<div style='margin:4px 0'>"
            f"<span style='display:inline-block;width:80px'>{d['date'][5:]}</span>"
            f"<span style='display:inline-block;height:20px;"
            f"width:{pct}%;background:#4a90d9;border-radius:3px;"
            f"text-align:right;color:#fff;font-size:12px;"
            f"padding-right:4px'>{d['count']}</span>"
            f"</div>\n"
        )

    # Top 用户
    top_rows = ""
    for i, u in enumerate(stats["top_users"][:10], 1):
        top_rows += (
            f"<tr><td>{i}</td>"
            f"<td title='{u['user_id']}'>{u['user_id'][:24]}</td>"
            f"<td>{u['count']}</td></tr>\n"
        )

    return f"""<!DOCTYPE html>
<html lang='zh-CN'>
<head><meta charset='utf-8'>
<title>恩特小助手 - 使用统计</title>
<style>
body {{ font-family: -apple-system, 'Microsoft YaHei', sans-serif;
       margin: 20px; background: #f5f5f5; color: #333; }}
h1 {{ color: #2c3e50; }}
.card {{ background: #fff; border-radius: 8px; padding: 20px; margin: 16px 0;
         box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
         gap: 16px; }}
.stat-box {{ text-align: center; padding: 20px; }}
.stat-num {{ font-size: 36px; font-weight: bold; color: #4a90d9; }}
.stat-label {{ font-size: 14px; color: #888; margin-top: 4px; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #eee; }}
th {{ background: #f8f9fa; font-weight: 600; }}
tr:hover {{ background: #f0f7ff; }}
.nav {{ margin-bottom: 16px; }}
.nav a {{ color: #4a90d9; text-decoration: none; margin-right: 16px; }}
.nav a:hover {{ text-decoration: underline; }}
</style></head>
<body>
<div class='nav'>
    <a href='/admin{pw_param}'>📂 管理页面</a>
    <a href='/admin/stats{pw_param}'>📊 使用统计</a>
</div>
<h1>📊 使用统计</h1>

<div class='grid'>
    <div class='card stat-box'>
        <div class='stat-num'>{stats['total_users']}</div>
        <div class='stat-label'>累计用户</div>
    </div>
    <div class='card stat-box'>
        <div class='stat-num'>{stats['active_today']}</div>
        <div class='stat-label'>今日活跃</div>
    </div>
    <div class='card stat-box'>
        <div class='stat-num'>{stats['total_messages']}</div>
        <div class='stat-label'>累计消息数</div>
    </div>
    <div class='card stat-box'>
        <div class='stat-num'>{len(users)}</div>
        <div class='stat-label'>注册用户</div>
    </div>
</div>

<div class='card'>
    <h2>📈 每日消息趋势（近 7 天）</h2>
    {'<p>暂无数据</p>' if not stats['daily_trend'] else trend_bars}
</div>

<div class='card'>
    <h2>👤 对话量 Top 10</h2>
    <table>
        <tr><th>#</th><th>用户</th><th>消息数</th></tr>
        {'<tr><td colspan="3">暂无数据</td></tr>' if not stats['top_users'] else top_rows}
    </table>
</div>

<div class='card'>
    <h2>👥 所有用户</h2>
    <table>
        <tr><th>用户ID</th><th>昵称</th><th>职位</th><th>身份</th>
            <th>部门</th><th>消息数</th><th>最后活跃</th></tr>
        {user_rows if user_rows else '<tr><td colspan="7">暂无用户</td></tr>'}
    </table>
</div>

<p style='color:#888;font-size:12px;margin-top:24px'>
    数据来源：user_store.db SQLite · 更新时间：{stats.get('_generated_at', '')}
</p>
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
        "uploads":     ("data/uploads", "standards"),
        "standards":   ("data/standards", "standards"),
        "fault_codes": ("data/fault_codes", "error_codes"),
    }
    result: dict = {"directories": {}}

    for dir_key, (rel_dir, default_coll) in scan_dirs.items():
        abs_dir = os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "..", rel_dir
        ))
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
    sync_all: bool = Form(False),
    force: bool = Form(False),
    password: str = Form(""),
):
    """手动触发文件同步

    - file_path 指定单个文件
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
        if not os.path.isfile(file_path):
            raise HTTPException(400, f"文件不存在: {file_path}")

        fname = os.path.basename(file_path)
        target = collection or "standards"

        # 提交后台任务（不做 Chroma 预检查，即使已入库也走 MinerU 获取最新 ZIP）
        # Chroma 入库时自带 ID 去重，不会重复写入
        from .task_manager import get_manager as get_task_manager
        manager = get_task_manager()
        record = manager.submit(
            file_name=fname,
            collection=target,
            process_fn=lambda: _run_sync_task(
                file_path, fname, target, force,
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
                   target: str, force: bool) -> object:
    """后台执行同步任务（被 task_manager 调用）

    独立的模块级函数，在后台线程中执行，自动关联进度上报。
    """
    from .engine import process_file
    from .sync_tracker import SyncTracker
    tracker = SyncTracker()

    try:
        fsize = os.path.getsize(file_path)
        fhash = str(int(os.path.getmtime(file_path)))
    except OSError:
        fsize, fhash = 0, ""

    tracker.upsert_file(file_path, fname, fsize, fhash, target)

    from .task_manager import report_progress as _rp
    _rp("syncing", 10, "开始同步处理...")

    doc = process_file(file_path, file_name=fname,
                       target_collection=target)

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


@router.get("/sync-stats")
def sync_stats(password: str = Query("", description="管理员密码")):
    """同步统计概览"""
    if not _verify_admin_access(password):
        raise HTTPException(401, "密码错误")

    from .sync_tracker import SyncTracker
    tracker = SyncTracker()
    stats = tracker.get_stats()

    # 补充目录文件数
    for dir_key, rel_dir in [("files_in_uploads", "data/uploads"),
                              ("files_in_standards", "data/standards"),
                              ("files_in_fault_codes", "data/fault_codes")]:
        abs_dir = os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "..", rel_dir
        ))
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
