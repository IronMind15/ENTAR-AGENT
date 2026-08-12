"""看板模板生成（v1.12.0）— 描述成模板 + 提交模板解析

两条把「用户想要的输出格式」变成模板定义（DashboardTemplate 的字段）的路径：

1. describe_to_spec(description, llm_func) — 自然语言描述 → LLM 生成
   {name, map_instructions, reduce_instructions, section_spec}。格式描述是
   开放式语言，正则扛不动，这里用一次 LLM（LLM 只用在刀刃上：仅描述路径调）。

2. parse_template_file(file_path) — 上传 Excel 表头行 / Markdown 标题 →
   确定性解析成 section_spec（无 LLM，可测可回归）。关键词推断章节种类：
   要点/摘要/今日→headline，提醒/异常/完整性→errors，来源/数据源→sources，
   风险/阻塞/待决策→risk 结论区，其余→进展结论区。缺的核心章节自动补齐，
   保证报告结构完整。
"""

import json
import logging
import os
import re

logger = logging.getLogger("dashboard.template_builder")

# ===== 章节种类关键词 =====
# 只保留「总结性名词」做 headline 信号——「今日/本周/本月」这类时间词太宽泛，
# 会把「本周进展」「今日进展」误判成要点区（2026-08-12 测试发现）
_HEADLINE_KW = ("要点", "摘要", "总结", "总览", "概述", "一句话")
_ERRORS_KW = ("提醒", "异常", "警告", "完整性", "缺失", "告警", "注意")
_SOURCES_KW = ("来源", "数据源", "原文", "出处", "链接")
_RISK_KW = ("风险", "阻塞", "待决策", "决策", "卡点", "延期", "负责人")

_MAX_TITLE_LEN = 14


def _infer_kind(title: str) -> str:
    if any(k in title for k in _HEADLINE_KW):
        return "headline"
    if any(k in title for k in _ERRORS_KW):
        return "errors"
    if any(k in title for k in _SOURCES_KW):
        return "sources"
    return "claims"


def _is_risk(title: str) -> bool:
    return any(k in title for k in _RISK_KW)


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S | re.I)
    if m:
        text = m.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    value = json.loads(text)
    return value if isinstance(value, dict) else {}


# ===== 描述成模板（LLM） =====
def describe_to_spec(description: str, llm_func=None) -> dict:
    """自然语言描述 → 模板定义 dict；失败返回 {"ok": False, "message": "..."}"""
    desc = (description or "").strip()
    if not desc:
        return {"ok": False, "message": "请描述想要的看板格式，例如：负责人/今日进展/明日计划"}
    if llm_func is None:
        try:
            from skills.agent import call_deepseek as llm_func
        except Exception:
            llm_func = None
    if llm_func is None:
        return {"ok": False, "message": "当前无法调用 AI 生成模板，请稍后再试"}
    prompt = (
        "你是看板格式设计师。用户描述他想要的看板汇报格式，请生成一个 JSON 模板定义。\n"
        "section_spec 是数组，每项是 {\"kind\": \"headline|claims|errors|sources\", "
        "\"title\": \"章节名\", \"levels\": [...]}；\n"
        "  headline 顶部一句话要点；claims 结论列表（可多个章节分主题，"
        "levels 限收 risk|decision|update|info 中的级别，缺省全收）；\n"
        "  errors 数据完整性提醒；sources 数据来源。\n"
        "map_instructions 指导筛选阶段保留重要变化；reduce_instructions 指导汇总组织结论。\n"
        "section_spec 必须至少含一个 headline 和至少一个 claims；用户描述没提到提醒/来源时不要加 errors/sources。\n"
        f"用户描述：{desc[:200]}\n"
        "只返回一行 JSON，不要 Markdown：\n"
        '{"name": "模板名(≤8字)", "map_instructions": "…", "reduce_instructions": "…", '
        '"section_spec": [{"kind": "...", "title": "...", "levels": ["..."]}]}'
    )
    try:
        raw = llm_func(prompt, max_tokens=900)
        data = _extract_json(raw)
        return _validate_template_def(data, desc)
    except Exception as e:
        logger.warning(f"描述成模板 LLM 失败: {e}")
        return {"ok": False, "message": f"生成模板失败：{str(e)[:80]}"}


def _validate_template_def(data: dict, fallback_desc: str) -> dict:
    spec = data.get("section_spec") if isinstance(data.get("section_spec"), list) else None
    if not spec:
        return {"ok": False, "message": "AI 生成的模板缺少章节结构，请换个描述试试"}
    cleaned = []
    has_headline = has_claims = False
    for section in spec:
        if not isinstance(section, dict):
            continue
        kind = section.get("kind")
        title = str(section.get("title", "")).strip()[: _MAX_TITLE_LEN]
        if not title:
            continue
        if kind == "headline":
            has_headline = True
            cleaned.append({"kind": "headline", "title": title})
        elif kind == "claims":
            has_claims = True
            levels = section.get("levels")
            if not (isinstance(levels, list) and levels
                    and all(l in ("risk", "decision", "update", "info") for l in levels)):
                levels = ["risk", "decision", "update", "info"]
            cleaned.append({"kind": "claims", "title": title, "levels": list(levels)})
        elif kind in ("errors", "sources"):
            cleaned.append({"kind": kind, "title": title})
    if not has_headline:
        cleaned.insert(0, {"kind": "headline", "title": "今日要点"})
    if not has_claims:
        cleaned.append({"kind": "claims", "title": "重点更新",
                        "levels": ["risk", "decision", "update", "info"]})
    # 核心章节兜底：报告结构完整（不因 AI 漏配而丢来源/完整性）
    if not any(s["kind"] == "errors" for s in cleaned):
        cleaned.append({"kind": "errors", "title": "数据完整性提醒"})
    if not any(s["kind"] == "sources" for s in cleaned):
        cleaned.append({"kind": "sources", "title": "数据来源"})
    name = str(data.get("name", "")).strip()[:8] or "自定义模板"
    return {
        "ok": True,
        "name": name,
        "description": fallback_desc[:80],
        "map_instructions": str(data.get("map_instructions", "")).strip()[:300],
        "reduce_instructions": str(data.get("reduce_instructions", "")).strip()[:300],
        "section_spec": cleaned,
    }


# ===== 提交模板解析（确定性，无 LLM） =====
_SUPPORTED_EXT = (".xlsx", ".xls", ".md", ".csv", ".txt")


def parse_template_file(file_path: str, file_name: str = "") -> dict:
    """上传文件（Excel 表头行 / Markdown 标题 / 文本行）→ 模板定义

    Returns: {"ok": True, name, description, map_instructions,
              reduce_instructions, section_spec} 或 {"ok": False, message}
    """
    if not file_path or not os.path.exists(file_path):
        return {"ok": False, "message": "找不到该文件，请重新上传后再试"}
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in _SUPPORTED_EXT:
        return {"ok": False,
                "message": f"暂只支持从 {', '.join(_SUPPORTED_EXT)} 文件识别模板格式，"
                           "请上传 Excel 或 Markdown 文件"}
    if ext in (".xlsx", ".xls"):
        titles = _excel_header_row(file_path)
    elif ext == ".md":
        titles = _markdown_headings(file_path)
    else:
        titles = _text_lines(file_path)
    return _titles_to_template(titles, file_name)


def _excel_header_row(file_path: str) -> list[str]:
    wb = None
    try:
        import openpyxl
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        ws = wb.active
        for row in ws.iter_rows(values_only=True):
            values = [str(v).strip() for v in row
                      if v is not None and str(v).strip()]
            if values:
                return values
    except Exception as e:
        logger.warning(f"读 Excel 表头失败 {file_path}: {e}")
    finally:
        # 必须关 workbook——Windows 上不关会锁文件，导致临时目录清理/后续重试失败
        if wb is not None:
            try:
                wb.close()
            except Exception:
                pass
    return []


def _markdown_headings(file_path: str) -> list[str]:
    titles = []
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r"^\s{0,3}#{1,3}\s+(.+?)\s*$", line)
                if m:
                    titles.append(m.group(1).strip())
    except Exception:
        pass
    return titles


def _text_lines(file_path: str) -> list[str]:
    lines = []
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip().strip("|")
                if line and len(line) <= _MAX_TITLE_LEN:
                    lines.append(line)
    except Exception:
        pass
    return lines[:30]


def _titles_to_template(titles: list[str], file_name: str = "") -> dict:
    titles = [t.strip() for t in titles if t and t.strip()]
    if not titles:
        return {"ok": False,
                "message": "没有识别到章节标题——Excel 用第一行表头，Markdown 用 # 标题"}
    spec = []
    headline_used = errors_used = sources_used = False
    for title in titles:
        title = title[: _MAX_TITLE_LEN]
        kind = _infer_kind(title)
        if kind == "headline":
            if not headline_used:
                spec.append({"kind": "headline", "title": title})
                headline_used = True
            continue
        if kind == "errors":
            if not errors_used:
                spec.append({"kind": "errors", "title": title})
                errors_used = True
            continue
        if kind == "sources":
            if not sources_used:
                spec.append({"kind": "sources", "title": title})
                sources_used = True
            continue
        levels = ["risk", "decision"] if _is_risk(title) \
            else ["update", "info"]
        spec.append({"kind": "claims", "title": title, "levels": levels})
    if not headline_used:
        spec.insert(0, {"kind": "headline", "title": "今日要点"})
    if not any(s["kind"] == "claims" for s in spec):
        spec.append({"kind": "claims", "title": "重点更新",
                     "levels": ["risk", "decision", "update", "info"]})
    if not errors_used:
        spec.append({"kind": "errors", "title": "数据完整性提醒"})
    if not sources_used:
        spec.append({"kind": "sources", "title": "数据来源"})
    base = os.path.splitext(os.path.basename(file_name or "模板"))[0][:8]
    return {
        "ok": True,
        "name": base or "自定义模板",
        "description": f"从文件《{file_name or base}》识别的模板",
        "map_instructions": ("优先筛选 priority=changed 的今日变化；"
                             "存量只保留持续风险、阻塞或待决策事项"),
        "reduce_instructions": ("按文件章节组织结论；每条结论必须可回溯原值"),
        "section_spec": spec,
    }
