"""
工具：search_knowledge_base
搜索 PCS 故障知识库。
当用户问 PCS 故障代码（d4-1 等格式）、告警、停机、不启动等设备异常现象时使用。
包含故障代码的名称、原因、地址、位地址等详细信息。
只含 PCS 产品故障数据，不含行业标准或技术规范。
"""

import json
import logging

from tools import register

logger = logging.getLogger("tool.kb")

DEFINITION = {
    "name": "search_knowledge_base",
    "description": (
        "搜索 PCS 故障知识库。当用户问 PCS 故障代码（d4-1 等格式）、告警、停机、"
        "不启动等设备异常现象时使用。包含故障代码的名称、原因、地址、位地址等详细信息。"
        "只含 PCS 产品故障数据，不含行业标准或技术规范。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词，使用用户问题中的核心词（故障代码、现象描述、文档标题等）。",
            }
        },
        "required": ["query"],
    },
}


@register("search_knowledge_base", DEFINITION)
def execute(args: dict) -> str:
    """执行故障知识库搜索"""
    query = (args.get("query") or "").strip()
    if not query:
        return json.dumps(
            {"error": "搜索关键词为空，请提供要搜索的内容"}, ensure_ascii=False
        )

    logger.info(f"  工具调用: search_knowledge_base(query={query})")

    # 延迟导入避免循环依赖
    from skills.error_query import search_kb as search_fault_kb

    results = search_fault_kb(query)
    if not results:
        return json.dumps(
            {
                "found": False,
                "results": [],
                "message": f"未找到与「{query}」相关的故障信息",
            },
            ensure_ascii=False,
        )

    # 清理内部字段（_ 开头的供内部使用，不给 LLM 看）+ 生成 source_label
    clean_results = []
    for r in results:
        item = {k: v for k, v in r.items() if not k.startswith("_")}
        # 生成引用标签供 LLM 直接复制
        fault_code = item.get("fault_code", "")
        name = item.get("name", "")
        if fault_code:
            item["source_label"] = f"[PCS故障 {fault_code} {name}]"
        else:
            row_num = item.get("row_num", "")
            item["source_label"] = f"[PCS参数表 第{row_num}行]"
        clean_results.append(item)

    return json.dumps({"found": True, "results": clean_results}, ensure_ascii=False)
