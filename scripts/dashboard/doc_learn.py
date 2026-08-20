"""
钉钉文档内容入库（v1.11.0）

「帮我学习」→ 取未学候选 → 读取全文 → notable 逐条 label:value 拼 Markdown →
engine.process_text 入库 → 标记 learned。

学习路径走纯文本直入（不写临时文件、不进 data/uploads），避免触发文件生命周期。
"""

import logging

logger = logging.getLogger("dashboard.doc_learn")


def learn_dingtalk_doc(user_id: str, client=None, kb=None) -> dict:
    """入库用户最近一个未学习的钉钉文档候选

    kb: 可选，指定知识库 dict（v1.11.5 多库），其 collection/department
        覆盖默认库（standards / public）。

    Returns:
        {"has_candidate": bool, "ok": bool, "message": str,
         "chunk_count": int, "records": int}
    """
    from .doc_candidates import get_candidate_store

    store = get_candidate_store()
    cand = store.get_pending(user_id)
    if not cand:
        return {"has_candidate": False, "ok": False,
                "message": "没有待学习的钉钉文档，请先发送文档链接。",
                "chunk_count": 0, "records": 0}

    if client is None:
        from scripts.dingtalk_doc_client import get_doc_client
        client = get_doc_client()
    try:
        result = client.read_document(
            cand.url, operator_id=cand.operator_union or user_id)
    except Exception as e:
        return {"has_candidate": True, "ok": False,
                "message": f"文档读取失败：{e}", "chunk_count": 0, "records": 0}
    if not result.get("ok"):
        return {"has_candidate": True, "ok": False,
                "message": result.get("message", "文档读取失败"),
                "chunk_count": 0, "records": 0}

    records = result.get("records") or []
    if not records:
        return {"has_candidate": True, "ok": False,
                "message": "文档无内容（0 条记录）", "chunk_count": 0, "records": 0}

    # v1.11.1：普通文档（doc）走 blocks→Markdown 保真全文入库，
    # 跳过逐条拼 Markdown（段落/表格格式不统一，保真更可靠）。
    if result.get("kind") == "doc" and result.get("markdown"):
        md = result["markdown"]
    else:
        md = _records_to_markdown(records)
    node_id = cand.node_id or result.get("node_id", "")
    file_name = f"钉钉文档_{node_id[:8]}.md"

    from scripts.doc_mgr.engine import process_text
    # v1.11.5：指定知识库则入库到该库（collection/department 由 kb 提供），
    # 未指定默认标准知识库 standards / public。
    if kb:
        target_collection = kb.get("collection") or kb.get("key")
        department = kb.get("department") or "public"
    else:
        target_collection = "standards"
        department = "public"
    try:
        doc = process_text(md, file_name=file_name,
                           target_collection=target_collection,
                           department=department)
    except Exception as e:
        return {"has_candidate": True, "ok": False,
                "message": f"入库失败：{e}", "chunk_count": 0, "records": len(records)}

    if doc.status != "done":
        return {"has_candidate": True, "ok": False,
                "message": doc.message or "入库未完成",
                "chunk_count": 0, "records": len(records)}

    store.mark_learned(cand.id)
    return {"has_candidate": True, "ok": True,
            "message": f"已入库 {doc.chunk_count} 块（{len(records)} 条记录）",
            "chunk_count": doc.chunk_count, "records": len(records)}


def _cell_to_text(v) -> str:
    """单元格值 → 文本（dict/list 统一为可读字符串）"""
    from .parser import extract_cell_value
    return extract_cell_value(v)


def _records_to_markdown(records: list[dict]) -> str:
    """AI表格记录 → Markdown：每条一个二级标题，字段 label：value 列表"""
    lines = []
    for i, rec in enumerate(records, 1):
        cells = rec.get("cells") or rec.get("fields") or rec
        if not isinstance(cells, dict):
            continue
        lines.append(f"## 记录 {i}")
        lines.append("")
        for k, v in cells.items():
            text = _cell_to_text(v)
            if text:
                lines.append(f"- {k}：{text}")
        lines.append("")
    return "\n".join(lines).strip()
