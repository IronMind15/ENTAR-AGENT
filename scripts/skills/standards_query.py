"""
标准查询模块 — Chroma standards collection 搜索

为 RAG Agent 提供 search_kb 函数（供 kb_search 工具分发调用，v1.12.0 改名），
用于搜索储能变流器/光伏行业标准（GB、IEC、EN 等）。

与 error_query.py 的 search_kb 一一对应，兼容同一调用约定。
"""

import logging
import os
import re
import sys

if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

from center_config import get_center_name
from doc_mgr.storage import get_store
from skills import BaseSkill, register
from skills.enhanced_search import enhanced_query, parse_query_results

logger = logging.getLogger("standards_query")

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

# ===== 标准编号正则表达式 =====
# 匹配格式：GB/T 34133-2023, EN50178, IEC 60664-1, GB/T 16935.1-2008 等
STANDARD_ID_PATTERN = re.compile(
    r'((?:GB|GB/T|EN|IEC|ISO|ANSI|UL|DIN|BS|JIS|NF|UNI|SAA|CSA|KS)'
    r'[\s/]?[\d]+(?:[.\-]\d+)*(?:[-.]?\d{4})?)',
    re.IGNORECASE
)

COLLECTION_NAME = "standards"

# 搜索参数
SEARCH_TOP_K = 5
DISTANCE_THRESHOLD_HIGH = 0.6
DISTANCE_THRESHOLD_MEDIUM = 0.9

# 搜索结果最小数量（低于此数量时提示用户换种问法）
MIN_RESULTS_THRESHOLD = 1

def _extract_standard_id(query: str) -> str | None:
    """从查询中提取标准编号"""
    m = STANDARD_ID_PATTERN.search(query)
    if m:
        return m.group(1).strip()
    return None


def _exact_match_by_std_id(std_id: str) -> dict | None:
    """精确匹配标准编号（metadata 过滤，不调向量搜索）

    支持模糊匹配：GB/T 34133-2023 可匹配 GB/T 34133-2023
    """
    store = get_store()

    # 尝试精确匹配
    try:
        result = store.get(COLLECTION_NAME, where={"std_id": std_id})
        if result and result.get("metadatas") and result["metadatas"]:
            meta = result["metadatas"][0]
            return {"metadata": meta}
    except Exception:
        pass

    # 尝试模糊匹配（去除空格和特殊字符）
    normalized_id = re.sub(r'[\s/\-.]', '', std_id.lower())
    try:
        # 获取所有记录进行客户端过滤
        all_results = store.get(COLLECTION_NAME)
        if all_results and all_results.get("metadatas"):
            for meta in all_results["metadatas"]:
                if meta and "std_id" in meta:
                    db_normalized = re.sub(r'[\s/\-.]', '', meta["std_id"].lower())
                    if db_normalized == normalized_id:
                        return {"metadata": meta}
    except Exception:
        pass

    return None


def search_kb(query: str, department: str = "", centers: list[str] | None = None) -> list[dict]:
    """搜索标准知识库，返回结构化标准信息列表（JSON 格式，供 LLM 工具调用使用）

    查询策略：
      1. 先尝试精确标准编号匹配
      2. 未命中则语义搜索（如有 centers 参数，按用户归属中心过滤 + 回退）

    Args:
        query: 搜索关键词
        department: （已废弃，保留兼容）单中心 ID
        centers: 用户归属中心列表，只返回这些部门 + 公共区的数据

    Returns:
        list[dict]: 每个 dict 包含标准信息（std_id, std_title, chapter 等）
    """
    full_text = query.strip()
    if not full_text:
        return []

    # ===== 快速通道：精确标准编号匹配 =====
    std_id = _extract_standard_id(full_text)
    if std_id:
        match = _exact_match_by_std_id(std_id)
        if match:
            meta = dict(match["metadata"])
            meta["_match_type"] = "exact"
            meta["_score"] = 0.0
            return [meta]

    # ===== 语义搜索 =====
    # 兼容旧参数：若传了 department 但没传 centers，转成列表
    if centers is None and department:
        centers = [department]
    results = _semantic_search(full_text, centers=centers)
    if results is None:
        return []
    return results


def _semantic_search(query: str, centers: list[str] | None = None) -> list[dict] | None:
    """执行语义搜索，支持按部门过滤 + 旧数据回退

    如果指定了归属中心列表，先尝试按用户中心 + public 过滤；
    结果不足时回退到不限制部门（兼容早期未打 department 标签的旧数据）。
    """
    try:
        # 1. 尝试按多中心过滤
        if centers:
            # 构建过滤条件：用户所有中心 + public（去重）
            filters = list(centers)
            if "public" not in filters:
                filters.append("public")
            filtered = enhanced_query(
                COLLECTION_NAME, query_text=query, n_results=SEARCH_TOP_K,
                where={"department": {"$in": filters}},
            )
            items = _parse_query_results(filtered)
            if len(items) >= MIN_RESULTS_THRESHOLD:
                return items
            # 结果不足，回退到无过滤（兼容旧数据）
            logger.info(
                f"  多中心过滤结果不足 ({len(items)}条)，回退到无条件搜索"
            )

        # 2. 无条件搜索（无中心 或 过滤回退）
        results = enhanced_query(
            COLLECTION_NAME, query_text=query, n_results=SEARCH_TOP_K,
        )
        return _parse_query_results(results)

    except Exception as e:
        logger.error(f"Chroma 语义搜索失败: {e}")
        return None


def _parse_query_results(results: dict) -> list[dict]:
    """解析 Chroma query 返回结果为统一格式（委托 enhanced_search 公共实现）"""
    return parse_query_results(results)


def format_standard_result(meta: dict) -> str:
    """格式化单条标准查询结果为可读文本（钉钉/Web 用）

    Args:
        meta: 标准 metadata dict

    Returns:
        格式化文本
    """
    std_id = meta.get("std_id", "-")
    std_title = meta.get("std_title", "-")
    chapter = meta.get("chapter", "-")
    chapter_title = meta.get("chapter_title", "-")
    page = meta.get("page", "-")
    file_name = meta.get("file_name", "-")
    confidence = meta.get("confidence", "text")
    content = meta.get("_content", meta.get("section_text", ""))
    score = meta.get("_score")

    department = meta.get("department", "")
    dept_tag = f"（{get_center_name(department)}）" if department and department != "public" else ""

    lines = []
    lines.append(f"【标准编号】{std_id}")
    lines.append(f"【标准名称】{std_title}{dept_tag}")
    lines.append(f"【章节】第{chapter}章{chapter_title}")

    if content:
        # 截取关键内容
        content_preview = content[:500].strip()
        lines.append(f"【内容】\n\n{content_preview}")

    lines.append("")
    lines.append("---")
    source_line = f"数据来源：{file_name} → 第{chapter}章 → 第{page}页"
    if confidence == "ocr":
        source_line += "（OCR识别，可能存在误差，请对照原文）"
    lines.append(source_line)

    return "\n".join(lines)


def format_standard_results(results: list[dict], query: str) -> str | None:
    """格式化多条标准查询结果

    Args:
        results: search_kb 返回的结果列表
        query: 用户原始查询

    Returns:
        格式化文本，或 None（无结果时）
    """
    if not results:
        return None

    # 检查结果数量是否达到最小阈值
    if len(results) < MIN_RESULTS_THRESHOLD:
        return None

    header = f"📋 找到 {len(results)} 条相关标准内容："
    result_blocks = []

    for i, meta in enumerate(results):
        score_tag = ""
        score = meta.get("_score")
        if score is not None:
            if score < DISTANCE_THRESHOLD_HIGH:
                score_tag = " [高匹配]"
            elif score < DISTANCE_THRESHOLD_MEDIUM:
                score_tag = " [中匹配]"

        block = format_standard_result(meta)

        result_blocks.append(
            f"────────── 结果 {i+1}{score_tag} ──────────\n{block}"
        )

    return header + "\n\n" + "\n\n".join(result_blocks)


def format_exact_result(meta: dict) -> str:
    """格式化精确匹配结果（与 error_query 兼容的接口）"""
    return format_standard_result(meta)


# ===== 公共 API（供 agent.py 等模块调用） =====

def extract_standard_id(query: str) -> str | None:
    """从查询中提取标准编号（供 agent.py 快速通道使用）"""
    return _extract_standard_id(query)


def exact_match_by_std_id(std_id: str) -> dict | None:
    """精确匹配标准编号（供 agent.py 快速通道使用）"""
    return _exact_match_by_std_id(std_id)


# ===== 技能类注册（v1.11.6 统一快速通道实现位置） =====

def _handle_impl(query: str) -> dict:
    """标准查询处理入口（技能 handle）

    与故障码技能对称的两级策略：
      1. 精确标准编号匹配（GB/T 34133-2023 等）→ 单条格式化秒回，不调 LLM
      2. 未命中 → 本地语义搜索兜底（不调 LLM，复用 search_kb 两级逻辑）
    """
    q = (query or "").strip()
    if not q:
        return {"answer": "请输入标准编号或标准名称关键词", "source": "standards"}

    results = search_kb(q)
    if not results:
        return {"answer":
                f"暂未找到与「{q}」相关的标准内容。\n\n"
                "可尝试标准编号（如 GB/T 34133-2023）或更换关键词；\n"
                "如需补充标准，请上传 PDF 后回复『帮我学习』入库。",
                "source": "standards"}

    if results[0].get("_match_type") == "exact":
        return {"answer": format_exact_result(results[0]),
                "source": "标准编号快速匹配"}

    formatted = format_standard_results(results, q)
    if formatted:
        return {"answer": formatted, "source": "标准语义搜索"}
    return {"answer": "暂未找到相关标准内容，可换个问法。", "source": "standards"}


@register
class StandardsQuerySkill(BaseSkill):
    """标准查询技能：标准编号精确匹配 + 语义搜索秒回（v1.11.6 统一快速通道）

    priority=70：低于故障码(100)/PCB(90)/看板(80)——标准号当初放 agent 内部
    兜底正是为避免含标准号的看板/PCB 场景被抢（切斯特顿栅栏）；高于 agent(50)
    兜底，纯标准查询不再调 LLM。
    """
    name = "标准查询"
    description = "精确匹配标准编号 + 标准语义搜索，秒回结果"
    priority = 70

    @classmethod
    def match(cls, query: str, user_id: str = "") -> bool:
        """含标准编号（GB/IEC/EN 等）才匹配"""
        return bool(STANDARD_ID_PATTERN.search((query or "").strip()))

    @classmethod
    def handle(cls, query: str, **kwargs) -> dict:
        return _handle_impl(query)


# 向后兼容：保持模块级 handle 函数（旧代码 from skills.standards_query import handle）
handle = _handle_impl
