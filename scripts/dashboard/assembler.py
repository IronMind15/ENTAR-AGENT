"""
看板 Markdown 组装（v1.11.0）

规则模板组装 → validate 拦截钉钉不支持的语法 → LLM 组装（注入 llm_func）→
validate 校验失败/异常一律规则兜底。遵循方法论 §5：关注置顶、长字段截断
200（parser 已截）、无表格语法、总字符数 ≤5000。
"""

import json
import logging
import re

logger = logging.getLogger("dashboard.assembler")

MAX_MARKDOWN_LEN = 5000
TABLE_SYNTAX_RE = re.compile(r"\|.*\|.*\|")

_CN_NUM = ("一", "二", "三", "四", "五", "六", "七", "八", "九", "十")


def _title_field(item: dict) -> str:
    """标题字段 = 第一个非空、非状态字段值（状态 label 含「状态」）"""
    for k, v in item.items():
        if "状态" not in k and v:
            return v
    return ""


def _status_of(item: dict) -> str:
    return next((v for k, v in item.items() if "状态" in k), "")


def _status_stat_line(status_counts: dict) -> str:
    if not status_counts:
        return ""
    return " / ".join(f"{k}{v}" for k, v in status_counts.items())


def _render_item(item: dict) -> list[str]:
    """渲染一条关注项：`1. 标题（状态）` + 缩进两格其余字段"""
    title = _title_field(item)
    status = _status_of(item)
    header = title or "（无名称）"
    if status:
        header = f"{header}（{status}）"
    lines = [f"1. {header}"]
    title_shown = False
    for k, v in item.items():
        if "状态" in k or not v:
            continue
        if not title_shown:
            title_shown = True   # 标题字段已作行首，跳过
            continue
        lines.append(f"   {k}：{v}")
    return lines


def _render_source_block(r: dict, idx: int) -> list[str]:
    lines = [f"## {_CN_NUM[idx]}、{r['name']}", ""]
    stat = _status_stat_line(r.get("status_counts", {}))
    lines.append(f"> 共 {r['total']} 条" + (f"｜{stat}" if stat else ""))
    lines.append("")

    att = r.get("attention_items", [])
    nor = r.get("normal_items", [])
    oth = r.get("other_items", [])

    if att:
        lines.append(f"### 🔴 关注（{len(att)}）")
        lines.append("")
        for it in att:
            lines.extend(_render_item(it))
        lines.append("")
    if nor:
        lines.append(f"### ✅ 正常（{len(nor)}）")
        lines.append("")
        shown = "、".join(t for t in (_title_field(x) for x in nor) if t)[:400]
        lines.append(f"- {shown}")
        lines.append("")
    if oth:
        lines.append(f"### 其他（{len(oth)}）")
        lines.append("")
        lines.append(f"- 共 {len(oth)} 条，未纳入关注/正常分类")
        lines.append("")
    return lines


def assemble_markdown(results: list[dict], title: str = "恩特能源每日项目看板",
                      date_str: str = "") -> str:
    """规则模板组装：板块按配置顺序，关注置顶，总字符数 ≤5000"""
    lines = [f"# {title}", ""]
    if date_str:
        lines.append(f"> 数据日期：{date_str}")
        lines.append("")
    for i, r in enumerate(results, 1):
        if i > 1:
            lines.append("---")
            lines.append("")
        lines.extend(_render_source_block(r, i - 1))
    text = "\n".join(lines).strip() + "\n"
    if len(text) > MAX_MARKDOWN_LEN:
        text = text[:MAX_MARKDOWN_LEN].rsplit("\n", 1)[0] + "\n…（内容过长已截断）"
    return text


def validate_markdown(text: str) -> tuple[bool, str]:
    """校验钉钉 markdown 约束：非空、≤5000、无表格语法

    Returns:
        (ok, reason)
    """
    if not text or not text.strip():
        return False, "看板内容为空"
    if len(text) > MAX_MARKDOWN_LEN:
        return False, f"看板超长（{len(text)} > {MAX_MARKDOWN_LEN}）"
    if TABLE_SYNTAX_RE.search(text):
        return False, "含表格语法（钉钉 Markdown 不支持）"
    return True, ""


def _build_llm_prompt(results: list[dict], title: str, date_str: str) -> str:
    """给 LLM 的结构化数据 + 格式要求"""
    data = []
    for r in results:
        block = {
            "板块": r["name"],
            "总条数": r["total"],
            "状态统计": r.get("status_counts", {}),
        }
        if r.get("attention_items"):
            block["关注项"] = r["attention_items"]
        if r.get("normal_items"):
            block["正常项"] = [_title_field(x) for x in r["normal_items"]]
        if r.get("other_items"):
            block["其他项数"] = len(r["other_items"])
        data.append(block)
    return (
        f"请把以下项目数据组装成钉钉机器人 Markdown 看板。\n"
        f"标题：{title}，数据日期：{date_str}。\n"
        "要求：\n"
        "1. 开头用 `📌 今日要点：` 给 2~3 行总结（哪些项目/问题最需关注、整体概况，"
        "不要只复述记录）；\n"
        "2. 关注项（滞后/故障/待验证等）置顶；每条用 `1. 标题（状态）` 开头，"
        "其余字段缩进两格 `  字段：值`；\n"
        "3. 状态统计写一行 `状态1数量 / 状态2数量`；\n"
        "4. 总字符数 ≤5000；不要使用 Markdown 表格（钉钉不支持 `|---|`）；\n"
        "不要输出任何解释，直接输出 Markdown 正文。\n"
        f"数据：{json.dumps(data, ensure_ascii=False)}"
    )


def llm_assemble(results: list[dict], title: str = "", date_str: str = "",
                 llm_func=None) -> str:
    """LLM 组装 → validate → 规则兜底

    llm_func 约定签名：llm_func(prompt: str) -> str，返回纯 Markdown 文本。
    为 None / 抛异常 / 未过 validate 一律规则兜底（不扩散异常）。
    """
    if llm_func is None:
        return assemble_markdown(results, title, date_str)
    try:
        text = llm_func(_build_llm_prompt(results, title, date_str)) or ""
        if validate_markdown(text)[0]:
            return text
        logger.warning("LLM 看板未过校验（空/超长/表格语法），规则兜底")
    except Exception as e:
        logger.warning(f"LLM 看板组装失败，规则兜底: {e}")
    return assemble_markdown(results, title, date_str)
