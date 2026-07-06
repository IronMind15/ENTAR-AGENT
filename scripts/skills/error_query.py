"""
故障查询模块（技能 1 — V2）
两级查询：
  1. 精确匹配故障代码 → 直接返回（不调 LLM，秒回）
  2. 语义搜索名称/原因/自然语言 → Chroma 检索 → 返回格式化结果
  3. 复杂自然语言 → DeepSeek 提取关键词 → 语义搜索
"""

import os
import re
import sys
import logging

# Windows UTF-8
if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

# 确保 scripts/ 在模块搜索路径中（用于 from config import ...）
_PARENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

import httpx
from chromadb import PersistentClient
from chromadb.utils import embedding_functions

from config import DEEPSEEK_API_KEY

logger = logging.getLogger("error_query")

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

# ===== 共享常量（被 main.py / dingtalk_bot.py import 使用） =====
# 故障代码正则 — 匹配 d4-1, df-8, d10-1~d10-16 等格式
FAULT_CODE_PATTERN = re.compile(r'(d[a-z0-9]+[-~]\d[\d~-]*)', re.IGNORECASE)

# 强故障关键词 — 只要命中就走故障查询，不走聊天
FAULT_KEYWORDS = [
    # 原有关键词
    "故障", "报错", "异常", "告警", "停机", "急停",
    # 新增常用说法
    "坏了", "报警", "错误", "断开", "跳闸", "失效",
    "没反应", "不工作", "不启动", "不发电", "断电",
    "不转", "不通", "漏电", "过热",
]

# 自然语言特征词 — 用来判断是否为复杂自然语言（需要 LLM 提取关键词）
NL_MARKERS = ["的", "了", "吗", "呢", "吧", "是", "怎么回事", "怎么",
              "为什么", "如何", "怎么办", "什么", "哪个", "报错",
              "故障", "查一下", "请问"]

# ===== 路径 =====
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
CHROMA_PATH = os.path.join(_PROJECT_ROOT, "knowledge_base")

# ===== Chroma 初始化 =====
client = PersistentClient(path=CHROMA_PATH)
ef = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="BAAI/bge-small-zh-v1.5"
)
collection = client.get_collection("error_codes", embedding_function=ef)


def call_deepseek(prompt: str, max_tokens: int = 200) -> str:
    """调 DeepSeek API（轻量调用，用于关键词提取）"""
    if not DEEPSEEK_API_KEY:
        logger.warning("DEEPSEEK_API_KEY 未配置，跳过 LLM 提取")
        return ""
    try:
        with httpx.Client(timeout=15) as c:
            r = c.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
                json={
                    "model": "deepseek-v4-flash",
                    "messages": [
                        {"role": "system", "content": "你是一个只输出关键词的工具，不要解释，不要多余内容。"},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.1,
                    "max_tokens": max_tokens,
                },
            )
            if r.status_code == 200:
                body = r.json()
                if body:
                    return body["choices"][0]["message"]["content"].strip()
            else:
                logger.warning(f"DeepSeek API 返回非 200: {r.status_code}")
    except httpx.TimeoutException:
        logger.warning("DeepSeek API 超时")
    except httpx.RequestError as e:
        logger.warning(f"DeepSeek API 请求失败: {e}")
    except Exception as e:
        logger.warning(f"DeepSeek API 未知错误: {e}")
    return ""


def _is_natural_language(query: str) -> bool:
    """判断查询是否为自然语言（需要 LLM 提取关键词）"""
    if len(query) <= 4:
        return False
    # 包含 NL 特征词
    return any(marker in query for marker in NL_MARKERS)


def _extract_fault_code(query: str) -> str | None:
    """从查询中提取故障代码"""
    m = FAULT_CODE_PATTERN.search(query)
    if m:
        return m.group(1).lower()
    return None


def _extract_keywords_with_llm(query: str) -> str:
    """用 DeepSeek 从自然语言中提取搜索关键词"""
    if not DEEPSEEK_API_KEY or not _is_natural_language(query):
        return query

    prompt = (
        "从以下查询中提取故障相关的搜索关键词（只返回关键词，不要多余内容）：\n"
        f"查询：{query}\n"
        "关键词："
    )

    result = call_deepseek(prompt, max_tokens=100)
    if result:
        return result
    return query


def _exact_match_by_code(fault_code: str) -> dict | None:
    """精确匹配故障代码（metadata 过滤，不调向量搜索）"""
    result = collection.get(where={"fault_code": fault_code})
    if result and result.get("metadatas") and result["metadatas"]:
        meta = result["metadatas"][0]
        return {"metadata": meta}
    return None


def _add_line_numbers(text: str) -> str:
    """给内容行加上序号（跳过空行、分隔线和数据来源行）"""
    lines = text.split("\n")
    num = 1
    result = []
    for line in lines:
        stripped = line.strip()
        if stripped == "" or stripped == "---" or stripped.startswith("数据来源"):
            result.append(line)
        else:
            result.append(f"{num}. {line}")
            num += 1
    return "\n".join(result)


def _format_meta_fields(meta: dict) -> list[str]:
    """格式化 metadata 中的扩展字段（地址、位地址、属性等），返回行列表

    被 _format_exact_result 和 _format_semantic_results 共用，避免重复。
    """
    fields = [
        ("地址", "address"),
        ("位地址", "bit_address"),
        ("属性", "attribute"),
        ("数据类型", "data_type"),
        ("默认值", "default_value"),
        ("备注", "notes"),
        ("说明", "description"),
        ("备注2", "notes2"),
    ]
    lines = []
    for label, key in fields:
        val = meta.get(key, "") or "-"
        lines.append(f"【{label}】{val}")
    return lines


def _format_exact_result(meta: dict) -> str:
    """格式化精确匹配结果（纯文本，不调 LLM）"""
    lines = []

    # 1~3. 核心信息（最关心的一眼看到）
    lines.append(f"【名称】{meta.get('name', '')}")
    lines.append(f"【故障原因】{meta.get('cause', '')}")
    lines.append(f"【故障代码】{meta.get('fault_code', '')}")
    lines.append("")

    # 4. 其他字段（通过共享函数实现，与语义搜索复用）
    lines.extend(_format_meta_fields(meta))

    lines.append("")
    # 5. 分割线
    lines.append("---")
    # 6. 数据来源（不带序号）
    sheet = meta.get("sheet_name", "")
    row = meta.get("row_num", "")
    lines.append(f"数据来源：PCS参数表 V1.6.2.xlsx → {sheet} → 第{row}行")

    return _add_line_numbers("\n".join(lines))


def _format_semantic_results(results: dict, query: str) -> str | None:
    """格式化语义搜索结果（每条结果独立编号从1开始）"""
    if not results or not results.get("documents") or not results["documents"][0]:
        return None

    docs = results["documents"][0]
    metas = results["metadatas"][0]
    dists = results.get("distances", [None])[0] if results.get("distances") else None

    header = f"🔍 找到 {len(docs)} 条相关信息："

    result_blocks = []

    for i in range(len(docs)):
        meta = metas[i] if metas and len(metas) > i else {}
        code = meta.get("fault_code", "")
        name = meta.get("name", "")
        cause = meta.get("cause", "")
        row = meta.get("row_num", "")
        sheet = meta.get("sheet_name", "")

        # 匹配度标记
        score_tag = ""
        if dists and len(dists) > i:
            s = dists[i]
            if s < 0.6:
                score_tag = " [高]"
            elif s < 0.9:
                score_tag = " [中]"

        # ---- 构建单条结果内容（独立编号） ----
        block_lines = []
        block_lines.append(f"【名称】{name or '-'}")
        block_lines.append(f"【故障原因】{cause or '-'}")
        block_lines.append(f"【故障代码】{code or '-'}")
        block_lines.append("")

        block_lines.extend(_format_meta_fields(meta))

        block_lines.append("")
        block_lines.append("---")
        block_lines.append(f"数据来源：PCS参数表 V1.6.2.xlsx → {sheet} → 第{row}行")

        # 每条结果独立从1开始编号
        numbered = _add_line_numbers("\n".join(block_lines))

        # 组装：结果标题 + 编号内容（合为一个字符串，避免多空行）
        result_blocks.append(
            f"────────── 结果 {i+1}{score_tag} ──────────\n{numbered}"
        )

    footer = (
        "\n\n⚠️ 如果以上结果未找到对应故障，可联系主管添加故障模式并更新，"
        "或换种说法尝试提问"
    )
    return header + "\n\n" + "\n\n".join(result_blocks) + footer


def handle(query: str) -> dict:
    """处理故障查询，返回 {answer, source}

    查询策略：
    1. 提取故障代码（如 d4-1）→ 精确匹配 → 直接返回（不调 LLM）
    2. 复杂自然语言 → DeepSeek 提取关键词 → 语义搜索
    3. 简单关键词 → 直接语义搜索
    """
    q = query.strip()
    if not q:
        return {"answer": "请输入故障代码或故障描述", "source": ""}

    # ===== 第 1 关：精确匹配故障代码 =====
    fault_code = _extract_fault_code(q)
    if fault_code:
        match = _exact_match_by_code(fault_code)
        if match:
            answer = _format_exact_result(match["metadata"])
            row = match["metadata"].get("row_num", "")
            return {"answer": answer, "source": f"遥信（DI）表 第{row}行"}

    # ===== 第 2 关：自然语言提取关键词 =====
    search_query = _extract_keywords_with_llm(q)

    # 如果 LLM 提取了关键词且与原文不同，给用户展示提取结果
    keyword_hint = ""
    if search_query != q and _is_natural_language(q):
        keyword_hint = (
            f"💡 提取的搜索关键词：{search_query}\n"
            "↙ 可根据此判断提取是否准确，结果不对可换种说法重新提问\n\n"
        )

    # ===== 第 3 关：语义搜索 =====
    results = collection.query(
        query_texts=[search_query],
        n_results=5,
    )
    formatted = _format_semantic_results(results, q)

    if formatted:
        return {"answer": keyword_hint + formatted, "source": "语义搜索"}

    # ===== 兜底：没找到 =====
    tip = (
        f"暂未找到与「{q}」相关的故障信息。\n\n"
        "请检查故障代码是否正确，或换一种描述方式。\n"
        "如需补充数据，请更新 Excel 后运行：python scripts/sync_kb.py"
    )
    return {"answer": keyword_hint + tip, "source": ""}
