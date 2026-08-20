"""
经验查询模块 — Chroma experience_kb collection 搜索

为 RAG Agent 提供 search_kb 函数（供 kb_search 工具分发调用，v1.12.0 改名），
用于搜索公司内部工程经验（维修记录、常见问题、故障排查经验）。

经验条目为五段式模板：故障现象 → 排查步骤 → 根因 → 解决方案 → 验证结果。
由 MarkdownChunker 按标题切块入库，metadata 带 std_title（条目标题）与
chapter_title（五段阶段名），检索时按阶段展示。

与 standards_query.py 的 search_kb 兼容同一调用约定（供 tools/ 工具调用）。
"""

import logging
import os
import sys

if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

from scripts.skills.enhanced_search import enhanced_query, parse_query_results

logger = logging.getLogger("experience_query")

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

COLLECTION_NAME = "experience_kb"

# 搜索参数
SEARCH_TOP_K = 5
DISTANCE_THRESHOLD_HIGH = 0.6
DISTANCE_THRESHOLD_MEDIUM = 0.9

# 搜索结果最小数量（低于此数量时提示用户换种问法）
MIN_RESULTS_THRESHOLD = 1


def search_kb(query: str, department: str = "", centers: list[str] | None = None) -> list[dict]:
    """搜索经验知识库，返回结构化经验条目块列表（JSON 格式，供 LLM 工具调用）

    本期不做部门隔离，忽略 centers，全公司共享。
    collection 不存在时由 enhanced_query / 存储层自动创建空库，返回 []。

    Args:
        query: 搜索关键词
        department: 保留兼容参数，本期忽略
        centers: 保留兼容参数，本期忽略

    Returns:
        list[dict]: 每个 dict 含经验块信息（std_title, chapter_title, file_name 等）
    """
    full_text = query.strip()
    if not full_text:
        return []

    try:
        results = enhanced_query(
            COLLECTION_NAME, query_text=full_text, n_results=SEARCH_TOP_K,
        )
        return _parse_query_results(results)
    except Exception as e:
        logger.error(f"经验库语义搜索失败: {e}")
        return []


def _parse_query_results(results: dict) -> list[dict]:
    """解析 Chroma query 返回结果为统一格式（委托 enhanced_search 公共实现）"""
    return parse_query_results(results)


def format_experience_result(meta: dict) -> str:
    """格式化单条经验查询结果为可读文本（钉钉/Web 用）

    Args:
        meta: 经验块 metadata dict（含 std_title/chapter_title/file_name/_content）

    Returns:
        格式化文本
    """
    title = meta.get("std_title", "-") or "-"
    stage = meta.get("chapter_title", "") or ""
    file_name = meta.get("file_name", "-")
    content = (meta.get("_content", "") or "").strip()

    lines = []
    lines.append(f"【标题】{title}")
    if stage:
        lines.append(f"【阶段】{stage}")
    if content:
        # 截取关键内容
        lines.append(f"【内容】\n\n{content[:500].strip()}")
    lines.append("")
    lines.append("---")
    lines.append(f"数据来源：经验库 → {file_name}")
    return "\n".join(lines)


def format_experience_results(results: list[dict], query: str) -> str | None:
    """格式化多条经验查询结果

    Args:
        results: search_kb 返回的结果列表
        query: 用户原始查询（当前未用，保留接口一致）

    Returns:
        格式化文本，或 None（无结果 / 未达最小阈值时）
    """
    if not results or len(results) < MIN_RESULTS_THRESHOLD:
        return None

    header = f"💡 找到 {len(results)} 条相关经验："
    result_blocks = []

    for i, meta in enumerate(results):
        score_tag = ""
        score = meta.get("_score")
        if score is not None:
            if score < DISTANCE_THRESHOLD_HIGH:
                score_tag = " [高匹配]"
            elif score < DISTANCE_THRESHOLD_MEDIUM:
                score_tag = " [中匹配]"

        block = format_experience_result(meta)
        result_blocks.append(
            f"────────── 结果 {i+1}{score_tag} ──────────\n{block}"
        )

    return header + "\n\n" + "\n\n".join(result_blocks)
