"""
⚠️ v1.11.5 已由通用 search_knowledge_base 替代（不再注册，本文件仅作参考）。

工具：search_experience_kb
搜索公司内部工程经验知识库（维修记录、常见问题、故障排查经验）。

经验条目为五段式：故障现象 → 排查步骤 → 根因 → 解决方案 → 验证结果。
当用户询问"怎么排查/怎么解决/遇到过吗/维修经验/常见问题怎么处理"时使用。
不包含 PCS 故障代码定义（用 search_knowledge_base）和行业标准规范（用 search_standards）。
多库改造后经验检索由 search_knowledge_base(query, knowledge_base='经验知识库') 承担。
"""

import json
import logging

from tools import register

logger = logging.getLogger("tool.experience")

DEFINITION = {
    "name": "search_experience_kb",
    "description": (
        "搜索公司内部工程经验知识库（维修记录、常见问题、故障排查经验）。"
        "包含：故障现象、排查步骤、根因、解决方案、验证结果 五段式经验条目。"
        "当用户询问'怎么排查''怎么解决''遇到过吗''维修经验''常见问题怎么处理'时使用。"
        "不包含 PCS 故障代码定义（用 search_knowledge_base）和行业标准规范（用 search_standards）。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词，使用用户问题核心词（设备/现象/代码/处理手法），如'IGBT 过温 排查'。",
            }
        },
        "required": ["query"],
    },
}


@register("search_experience_kb", DEFINITION)
def execute(args: dict) -> str:
    """执行经验知识库搜索（本期不做部门隔离，全公司共享）"""
    query = (args.get("query") or "").strip()
    if not query:
        return json.dumps(
            {"error": "搜索关键词为空，请提供要搜索的内容"}, ensure_ascii=False
        )

    logger.info(f"  工具调用: search_experience_kb(query={query})")

    # 延迟导入避免循环依赖
    from skills.experience_query import search_kb

    results = search_kb(query)
    if not results:
        return json.dumps(
            {
                "found": False,
                "results": [],
                "message": f"未找到与「{query}」相关的经验，可建议用户补充经验条目或换个问法",
            },
            ensure_ascii=False,
        )

    # 清理内部字段，保留 _content 给 LLM 参考（重命名为 content_summary）
    clean_results = []
    for r in results:
        item = {k: v for k, v in r.items() if not k.startswith("_") or k == "_content"}
        if "_content" in r:
            item["content_summary"] = r["_content"]
        # 生成引用标签
        title = item.get("std_title", "")
        chapter_title = item.get("chapter_title", "")
        file_name = item.get("file_name", "")
        label_parts = []
        if title:
            label_parts.append(title)
        if chapter_title:
            label_parts.append(chapter_title)
        if file_name:
            label_parts.append(file_name)
        item["source_label"] = f"[经验 {' | '.join(label_parts)}]" if label_parts else "[经验库]"
        clean_results.append(item)

    return json.dumps({"found": True, "results": clean_results}, ensure_ascii=False)
