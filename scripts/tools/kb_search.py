"""
工具：kb_search（v1.11.5 通用知识库查询，v1.12.0 改名）

多知识库改造后统一的查询入口：可在不同知识库选择查询，取代 v1.11.5 前的三个
独立查询工具（故障库 / 标准库 / 经验库）。

参数 knowledge_base 指定知识库名称或 key（如「故障知识库」「标准知识库」
「产品手册」）；留空则自动在所有「用户可见」的知识库中搜索并合并 top 结果——
LLM 无需预知库列表。

分发时保留各库专有逻辑：
  - error_codes    → error_query.search_kb（故障码精确匹配 + 语义）
  - standards      → standards_query.search_kb（std_id 精确 + 用户中心过滤）
  - experience_kb  → experience_query.search_kb（经验五段式）
  - 其他自定义库     → enhanced_query 通用检索（带 department 过滤，部门权限预留）
"""

import json
import logging

from tools import register

logger = logging.getLogger("tool.kb")

DEFINITION = {
    "name": "kb_search",
    "description": (
        "搜索企业知识库。统一入口，可在不同知识库之间选择查询。"
        "参数 knowledge_base 指定知识库名称或标识（如『故障知识库』『标准知识库』"
        "『经验知识库』或自定义库名），留空则自动在所有可见知识库中搜索并合并结果。"
        "用于：PCS 故障代码/故障原因、国家标准/行业规范、公司文档、工程经验案例等检索。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词，使用用户问题核心词（故障代码、现象描述、标准编号、文档标题等）。",
            },
            "knowledge_base": {
                "type": "string",
                "description": "指定知识库名称或标识（如『故障知识库』『标准知识库』『经验知识库』"
                               "或自定义库名）。留空则自动搜索所有可见知识库。",
            },
        },
        "required": ["query"],
    },
}

# 每库最多取 N 条，合并后全局最多取 N 条（避免跨库全量搜索撑爆上下文）
_PER_KB_LIMIT = 4
_TOTAL_LIMIT = 8


@register(
    DEFINITION,
    sector="kb",
    display="🔍 搜索知识库...",
    user_desc=(
        "搜索企业知识库（故障/标准/经验/自定义库）。用户问：故障代码（d4-1 等）、"
        "报错/告警/停机、标准编号（GB/T 34133、IEC 60664）、技术要求、公司文档、维修经验。\n"
        "参数 knowledge_base 指定库名（如「标准知识库」），拿不准留空自动全库搜。\n"
        '示例：d4-1 是什么故障 → kb_search(query="d4-1")'
    ),
)
def execute(args: dict) -> str:
    """通用知识库搜索"""
    query = (args.get("query") or "").strip()
    kb_arg = (args.get("knowledge_base") or "").strip()
    if not query:
        return json.dumps(
            {"error": "搜索关键词为空，请提供要搜索的内容"}, ensure_ascii=False
        )

    logger.info(f"  工具调用: kb_search(query={query}, kb={kb_arg or '全部'})")

    from kb_registry import get_visible_knowledge_bases, resolve_kb

    user_centers = None
    try:
        from tools import get_user_centers
        user_centers = get_user_centers()
    except Exception:
        pass
    visible = get_visible_knowledge_bases(user_centers)

    # 目标库集合
    targets = None
    if kb_arg:
        kb = resolve_kb(kb_arg)
        if not kb:
            names = "、".join(k["name"] for k in visible) or "（暂无）"
            return json.dumps({
                "found": False, "results": [],
                "message": f"未找到知识库「{kb_arg}」。当前可用知识库：{names}",
            }, ensure_ascii=False)
        visible_keys = {v.get("key") for v in visible}
        if kb.get("key") not in visible_keys:
            # 库存在但不可见（停用或部门权限预留，当前默认全 public 一般不触发）
            hint = "已停用" if not kb.get("enabled", 1) else \
                f"部门:{kb.get('department') or 'public'}不可见"
            return json.dumps({
                "found": False, "results": [],
                "message": f"知识库「{kb['name']}」当前不可用（{hint}）",
            }, ensure_ascii=False)
        targets = [kb]
    else:
        targets = visible or []

    if not targets:
        return json.dumps({
            "found": False, "results": [],
            "message": "当前没有可用的知识库，请先创建知识库或登记内容。",
        }, ensure_ascii=False)

    # 分发搜索 + 合并
    merged: list[dict] = []
    seen: set[tuple] = set()
    for kb in targets:
        try:
            items = _search_one(query, kb, user_centers)
        except Exception as e:
            logger.error(f"知识库 [{kb.get('name')}] 搜索失败: {e}")
            continue
        for it in items[: _PER_KB_LIMIT]:
            key = (kb.get("key"), it.get("source_label") or "")
            if key in seen:
                continue
            seen.add(key)
            it["kb_name"] = kb.get("name") or kb.get("key")
            merged.append(it)

    # 按相关度排序（_score 越小越相关，精确匹配为 0）
    merged.sort(key=lambda x: (x.get("_score") if x.get("_score") is not None
                               else float("inf")))
    merged = merged[:_TOTAL_LIMIT]

    # 移除内部 _ 字段（保留 _content → content_summary 供 LLM 参考）
    clean = []
    for r in merged:
        item = {k: v for k, v in r.items() if not k.startswith("_") or k == "_content"}
        if "_content" in r:
            item["content_summary"] = r["_content"]
        clean.append(item)

    if not clean:
        return json.dumps({
            "found": False, "results": [],
            "message": f"未找到与「{query}」相关的知识，可换个问法或补充内容入库",
        }, ensure_ascii=False)

    return json.dumps({"found": True, "results": clean}, ensure_ascii=False)


def _search_one(query: str, kb: dict, centers: list[str] | None) -> list[dict]:
    """在单个知识库中检索，返回统一 metadata 列表（带 _score / source_label）"""
    coll = kb.get("collection") or kb.get("key")
    name = kb.get("name") or kb.get("key")

    if coll == "error_codes":
        from skills.error_query import search_kb as f
        results = f(query)
        for it in results:
            it["source_label"] = _error_label(it)
        return results

    if coll == "standards":
        from skills.standards_query import search_kb as f
        results = f(query, centers=centers)
        for it in results:
            it["source_label"] = _standard_label(it)
        return results

    if coll == "experience_kb":
        from skills.experience_query import search_kb as f
        results = f(query)
        for it in results:
            it["source_label"] = _experience_label(it)
        return results

    # 自定义库：通用增强检索（部门权限预留：非 public 库按库的部门过滤）
    from skills.enhanced_search import enhanced_query
    where = None
    dept = kb.get("department") or "public"
    if dept != "public":
        where = {"department": dept}
    raw = enhanced_query(coll, query_text=query, n_results=_PER_KB_LIMIT,
                         where=where)
    results = []
    if raw and raw.get("documents") and raw["documents"][0]:
        for i in range(len(raw["documents"][0])):
            meta = dict(raw["metadatas"][0][i])
            if raw.get("distances"):
                meta["_score"] = round(float(raw["distances"][0][i]), 4)
            meta["_match_type"] = "semantic"
            meta["_content"] = raw["documents"][0][i][:2000]
            meta["source_label"] = _custom_label(meta, name)
            results.append(meta)
    return results


def _error_label(meta: dict) -> str:
    fault_code = meta.get("fault_code", "")
    fault_name = meta.get("name", "")
    if fault_code:
        return f"[故障 {fault_code} {fault_name}]"
    row_num = meta.get("row_num", "")
    return f"[PCS参数表 第{row_num}行]" if row_num else "[故障知识库]"


def _standard_label(meta: dict) -> str:
    parts = []
    if meta.get("std_id"):
        parts.append(meta["std_id"])
    if meta.get("chapter"):
        parts.append(f"第{meta['chapter']}章")
    if meta.get("chapter_title"):
        parts.append(meta["chapter_title"])
    if meta.get("page"):
        parts.append(f"第{meta['page']}页")
    return f"[标准 {' '.join(parts)}]" if parts else "[标准知识库]"


def _experience_label(meta: dict) -> str:
    parts = [p for p in (
        meta.get("std_title"), meta.get("chapter_title"), meta.get("file_name")
    ) if p]
    return f"[经验 {' | '.join(parts)}]" if parts else "[经验知识库]"


def _custom_label(meta: dict, kb_name: str) -> str:
    title = meta.get("std_title") or meta.get("title") or meta.get("file_name") or ""
    return f"[{kb_name} {title}]" if title else f"[{kb_name}]"
