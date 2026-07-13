"""
文档管理 API 路由

提供 REST API 供管理页面调用。
所有路由以 /admin 为前缀，由 main.py 挂载。
"""

import os
import uuid
import logging
from fastapi import APIRouter, UploadFile, File, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from .engine import process_file
from .storage import get_store
from .views import ADMIN_HTML

logger = logging.getLogger("doc_mgr.router")

router = APIRouter(prefix="/admin")

# 上传文件临时存储目录
UPLOAD_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "uploads"
)


@router.get("", response_class=HTMLResponse)
def admin_home():
    """管理页面首页"""
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


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """上传文件，自动处理入库

    支持格式: .pdf, .xlsx, .xls
    上传后自动调用 engine.process_file() 完成提取→切块→入库。
    """
    if not file.filename:
        raise HTTPException(400, "未提供文件")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in (".pdf", ".xlsx", ".xls"):
        raise HTTPException(400, f"不支持的文件类型: {ext}（仅支持 PDF/Excel）")

    # 保存临时文件
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex}_{file.filename}"
    save_path = os.path.join(UPLOAD_DIR, safe_name)

    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    # 处理
    try:
        doc = process_file(save_path, file_name=file.filename)
    except Exception as e:
        logger.exception(f"处理失败: {file.filename}")
        return JSONResponse({
            "status": "error",
            "file_name": file.filename,
            "message": f"处理异常: {str(e)}",
        }, status_code=500)

    return JSONResponse({
        "status": doc.status,
        "file_name": doc.file_name,
        "collection": doc.collection,
        "chunk_count": doc.chunk_count,
        "std_id": doc.std_id,
        "std_title": doc.std_title,
        "message": doc.message,
    })


@router.delete("/docs")
def delete_docs(
    collection: str = Query(..., description="collection 名"),
    file_name: str = Query("", description="要删除的文件名（空则清空整个 collection）"),
):
    """删除文档

    - 指定 file_name：删除该文件的所有切块
    - 不指定 file_name：清空整个 collection
    """
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
