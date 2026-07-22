"""
工具：search_standards
搜索储能变流器/光伏行业标准。
包含国家标准(GB/T、GB)、行业标准、国际标准(IEC、EN)等，
覆盖安全要求、并网要求、检测方法、电气性能、绝缘配合等技术规范内容。
当用户询问国家标准、行业规范、技术要求或标准编号时使用。
不包含 PCS 故障代码。
"""

import json
import logging

from tools import register

logger = logging.getLogger("tool.standards")

DEFINITION = {
    "name": "search_standards",
    "description": (
        "搜索储能变流器/光伏行业标准。包含国家标准(GB/T、GB)、行业标准、"
        "国际标准(IEC、EN)等，覆盖安全要求、并网要求、检测方法、电气性能、"
        "绝缘配合等技术规范内容。当用户询问国家标准、行业规范、技术要求或标准编号时使用。"
        "不包含 PCS 故障代码。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词，使用用户问题中的核心词（标准编号、术语、检测项目、技术指标等）。",
            }
        },
        "required": ["query"],
    },
}


@register("search_standards", DEFINITION)
def execute(args: dict) -> str:
    """执行标准文档搜索（感知当前用户归属中心，按中心隔离 + 公共区回退）"""
    query = (args.get("query") or "").strip()
    if not query:
        return json.dumps(
            {"error": "搜索关键词为空，请提供要搜索的内容"}, ensure_ascii=False
        )

    logger.info(f"  工具调用: search_standards(query={query})")

    # 读取当前用户的归属中心，实现按中心隔离查询
    from tools import get_user_centers
    user_centers = get_user_centers()
    if user_centers:
        logger.info(f"  按中心过滤: {user_centers}")

    # 延迟导入避免循环依赖
    from skills.standards_query import search_kb as search_std_kb

    results = search_std_kb(query, centers=user_centers)
    if not results:
        return json.dumps(
            {
                "found": False,
                "results": [],
                "message": f"未找到与「{query}」相关的标准信息",
            },
            ensure_ascii=False,
        )

    # 清理内部字段，保留 _content 给 LLM 参考（重命名为 content_summary）
    clean_results = []
    for r in results:
        item = {k: v for k, v in r.items() if not k.startswith("_") or k == "_content"}
        if "_content" in r:
            item["content_summary"] = r["_content"]
        clean_results.append(item)

    return json.dumps({"found": True, "results": clean_results}, ensure_ascii=False)
