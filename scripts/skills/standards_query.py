"""
标准查询模块 — Chroma standards collection 搜索

为 RAG Agent 提供 search_standards 工具函数，
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

from chromadb import PersistentClient
from chromadb.utils import embedding_functions

logger = logging.getLogger("standards_query")

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

# ===== 路径 =====
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
CHROMA_PATH = os.path.join(_PROJECT_ROOT, "knowledge_base")
COLLECTION_NAME = "standards"

# 搜索参数
SEARCH_TOP_K = 5
DISTANCE_THRESHOLD_HIGH = 0.6
DISTANCE_THRESHOLD_MEDIUM = 0.9

# ===== Chroma 客户端（延迟初始化）=====
_client = None
_ef = None
_collection = None


def _get_collection():
    """懒加载 Chroma standards collection"""
    global _client, _ef, _collection
    if _collection is not None:
        return _collection
    if _client is None:
        _client = PersistentClient(path=CHROMA_PATH)
        logger.info("Chroma 客户端已连接")
    if _ef is None:
        logger.info("加载 embedding 模型（约 30MB）...")
        _ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="BAAI/bge-small-zh-v1.5"
        )

    try:
        import chromadb
        _collection = _client.get_collection(COLLECTION_NAME, embedding_function=_ef)
    except (ValueError, chromadb.errors.NotFoundError):
        logger.warning(f"Chroma collection '{COLLECTION_NAME}' 不存在，请先运行 sync_standards.py")
        _collection = None
        return None

    logger.info(f"Chroma 就绪，共 {_collection.count()} 条标准记录")
    return _collection


def search_kb(query: str) -> list[dict]:
    """搜索标准知识库，返回结构化标准信息列表（JSON 格式，供 LLM 工具调用使用）

    Args:
        query: 搜索关键词

    Returns:
        list[dict]: 每个 dict 包含标准信息（std_id, std_title, chapter 等）
    """
    full_text = query.strip()
    if not full_text:
        return []

    collection = _get_collection()
    if collection is None:
        return []

    try:
        results = collection.query(
            query_texts=[full_text],
            n_results=SEARCH_TOP_K,
        )
    except Exception:
        logger.exception("Chroma 语义搜索失败")
        return []

    if not results or not results.get("documents") or not results["documents"][0]:
        return []

    items = []
    for i in range(len(results["documents"][0])):
        meta = dict(results["metadatas"][0][i])
        if results.get("distances"):
            meta["_score"] = round(float(results["distances"][0][i]), 4)
        meta["_match_type"] = "semantic"
        meta["_content"] = results["documents"][0][i][:2000]  # 前2000字符供 LLM 参考
        items.append(meta)

    return items


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

    lines = []
    lines.append(f"【标准编号】{std_id}")
    lines.append(f"【标准名称】{std_title}")
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
