"""来源可核验的看板 LLM 流水线。

LLM 负责从完整非敏感业务数据中提炼重点；代码只做分批、证据校验和钉钉渲染，
不再用固定板块/固定字段规则提前裁掉数据。
"""

from __future__ import annotations

import json
import hashlib
import logging
import os
import re
import time
from concurrent.futures import (
    ThreadPoolExecutor, TimeoutError as FutureTimeout, as_completed,
)
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from .alerts import field_diff, make_snapshot
from .assembler import MAX_MARKDOWN_LEN, validate_markdown
from .template_store import (
    _DEFAULT_MAP_INSTRUCTION,
    _DEFAULT_REDUCE_INSTRUCTION,
    _DEFAULT_SECTION_SPEC,
)

logger = logging.getLogger("dashboard.llm_pipeline")
REPORT_MARKDOWN_LEN = MAX_MARKDOWN_LEN - 400

_MAP_EXECUTOR = ThreadPoolExecutor(
    max_workers=max(2, int(os.getenv("DASHBOARD_LLM_MAP_WORKERS", "2"))),
    thread_name_prefix="dashboard-map")
_REDUCE_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dashboard-reduce")


@dataclass
class DashboardReport:
    text: str
    snapshot: list[dict]
    changes: list[dict]
    verification: dict
    messages: list[str]


def _plain_text(value, max_chars: int | None = None) -> str:
    """转成主动消息安全文本；Markdown 表格改为普通文本，避免 `|` 原样泄漏。"""
    text = str(value or "").replace("\r", "").replace("<br/>", " ").replace("<br>", " ").strip()
    rows = []
    for line in text.split("\n"):
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) > 1:
            if all(re.fullmatch(r":?-{3,}:?", cell or "-") for cell in cells):
                continue
            line = "；".join(cell for cell in cells if cell)
        rows.append(line.replace("|", "｜"))
    text = " ".join(part for part in rows if part).strip()
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


def _truncate_sentence(value, max_chars: int = 400) -> str:
    """证据原值截断：先净化表格，超长时按句边界截（不砍半句话）。

    v1.12.x：修复「依据原值」行 160 字硬截加「…」——长字段（问题点描述、
    周报内容）普遍 300~2000 字，硬截导致条目像没写完。句末标点处截断，
    找不到边界才退化为硬截。
    """
    text = _plain_text(value)
    if len(text) <= max_chars:
        return text
    head = text[:max_chars]
    cut = max(head.rfind(ch) for ch in ("。", "；", "！", "？"))
    if cut > max_chars * 0.5:
        head = head[:cut + 1]
    return head.rstrip() + "…"


def _json_object(text: str) -> dict:
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S | re.I)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("LLM 返回不是 JSON 对象")
    return value


def _chunks(records: list[dict], batch_size: int,
            max_batch_chars: int) -> list[list[dict]]:
    batches, current, chars = [], [], 0
    for record in records:
        size = len(json.dumps(record, ensure_ascii=False, default=str))
        if current and (len(current) >= batch_size or chars + size > max_batch_chars):
            batches.append(current)
            current, chars = [], 0
        current.append(record)
        chars += size
    if current:
        batches.append(current)
    return batches


def _catalog(results: list[dict]) -> tuple[dict, dict, list[dict]]:
    """生成 LLM 引用 ID → 短引用的唯一映射。"""
    valid, labels, records = {}, {}, []
    for source_index, result in enumerate(results, 1):
        source_key = result.get("source_key", "")
        labels[source_key] = f"S{source_index}"
        metric_fields = {"总条数": str(result.get("total", 0))}
        metric_fields.update({f"状态统计.{k}": str(v)
                              for k, v in result.get("status_counts", {}).items()})
        valid[source_key] = {
            "evidence": result.get("source_meta", {}), "fields": metric_fields,
        }
        for record_index, item in enumerate(result.get("detailed_items", []), 1):
            evidence = item.get("evidence", {})
            record_id = str(evidence.get("record_id", ""))
            ref = f"{source_key}:{record_id}"
            label = f"S{source_index}-R{record_index}"
            valid[ref] = {"evidence": evidence, "fields": item.get("fields", {})}
            labels[ref] = label
            records.append({
                "ref": ref,
                "source": result.get("name", source_key),
                "fields": item.get("fields", {}),
                "evidence": evidence,
            })
    return valid, labels, records


def _prompt(stage: str, payload: dict, task_prompt: str = "") -> str:
    # 不在日志中重复记录用户提示词正文；记录哈希即可让“任务首条展示的提示词”、
    # 模型实际输入和推送留档三者相互核对。stage 会在 map/reduce 各记录一次。
    prompt_hash = hashlib.sha256((task_prompt or "").encode("utf-8")).hexdigest()[:16]
    logger.info("[看板执行模型] stage=%s 已注入固定提示词 hash=%s chars=%s",
                stage, prompt_hash, len(task_prompt or ""))
    output_schema = (
        {"selected_refs": ["source_key:record_id"]}
        if stage == "map" else {
            "headline": "一句话总览",
            "claims": [{
                "text": "可直接给老板看的精准结论",
                "level": "risk|decision|update|info",
                "refs": ["source_key:record_id"],
                "evidence": [{"ref": "source_key:record_id", "field": "字段名"}],
            }],
        }
    )
    envelope = {
        "stage": stage,
        "role": "企业内部每日经营看板分析员",
        "rules": [
            "只依据输入数据，不补写、猜测或美化不存在的事实",
            "map阶段只筛选最值得上报的记录ID，不写结论；无论输入多少记录，最多返回8个ID",
            "reduce阶段每条结论必须带 refs；记录结论用 source_key:record_id",
            "聚合结论可引用 source_key，并明确统计口径",
            "evidence 只填写输入中真实存在的 ref 和字段名；字段原值由系统自动回填",
            "识别重要进展、风险、阻塞、待决策事项和负责人，不因缺少状态列而判定正常",
            "返回严格 JSON，不要 Markdown，不要解释",
        ],
        "output_schema": output_schema,
        "limits": {
            "max_claims": 8 if stage == "map" else 12,
            "max_claim_text_chars": 100,
            "max_evidence_per_claim": 3,
        },
        # 任务提示词是创建/明确改模板时固化的快照；payload 才是本次运行的
        # 临时文档数据。两者分开，便于审计“任务怎么要求”和“本次看到了什么”。
        "task_prompt": task_prompt,
        "payload": payload,
    }
    return json.dumps(envelope, ensure_ascii=False, default=str)


def _claims(value: dict) -> list[dict]:
    out = []
    for claim in value.get("claims", []) if isinstance(value, dict) else []:
        if not isinstance(claim, dict):
            continue
        text = str(claim.get("text", "")).strip()
        refs = [str(x) for x in claim.get("refs", []) if str(x).strip()] \
            if isinstance(claim.get("refs"), list) else []
        if text and refs:
            evidence = claim.get("evidence", [])
            out.append({"text": text, "level": str(claim.get("level", "info")),
                        "refs": refs,
                        "evidence": evidence if isinstance(evidence, list) else []})
    return out


def _selected_refs(value: dict) -> list[str]:
    refs = value.get("selected_refs", []) if isinstance(value, dict) else []
    if not isinstance(refs, list):
        return []
    return [str(ref) for ref in refs[:8] if str(ref).strip()]


def _preferred_evidence_field(fields: dict) -> tuple[str, object] | None:
    """选一项最能解释记录的字段，供受控的 evidence 自动回填使用。

    文档型来源常把 ``类型=表格`` 放在第一个字段；它只能说明载体，不能支撑
    一条业务结论。因此优先选择内容、事项、进展、问题、计划等语义字段，最后
    才退回第一个非空字段。这个函数不生成结论，只决定应回填哪条已有原值。
    """
    pairs = [(str(key), value) for key, value in (fields or {}).items()
             if value not in (None, "")]
    if not pairs:
        return None
    preferred = (
        "事项", "项目", "产品", "标题", "名称", "内容", "工作", "进展",
        "问题", "风险", "计划", "状态", "描述", "结论",
    )
    for needle in preferred:
        for key, value in pairs:
            if needle in key:
                return key, value
    for key, value in pairs:
        if key not in {"类型", "分类", "序号", "编号"}:
            return key, value
    return pairs[0]


def _verify(claims: list[dict], valid_refs: dict) -> tuple[list[dict], int, int]:
    """校验证据引用，并仅为“漏填 evidence”的有效引用自动回填原值。

    不能接受伪造/错误字段：只要模型给出了 evidence 但字段不存在，仍拒绝该
    结论。自动回填只处理模型已给出有效 refs、却完全遗漏 evidence 数组的情况；
    回填内容也只能来自这些 refs 中实际存在的字段。
    """
    accepted, rejected, repaired = [], 0, 0
    for claim in claims:
        refs = claim.get("refs", [])
        evidence_ok = set()
        normalized_evidence = []
        for evidence in claim.get("evidence", []):
            if not isinstance(evidence, dict):
                continue
            ref = str(evidence.get("ref", ""))
            field = str(evidence.get("field", ""))
            actual = valid_refs.get(ref, {}).get("fields", {}).get(field)
            if actual is not None:
                evidence_ok.add(ref)
                normalized_evidence.append({
                    "ref": ref, "field": field, "value": str(actual),
                })
        if refs and all(ref in valid_refs and ref in evidence_ok for ref in refs):
            normalized = dict(claim)
            normalized["evidence"] = normalized_evidence
            accepted.append(normalized)
        elif refs and not claim.get("evidence") and all(ref in valid_refs for ref in refs):
            repaired_evidence = []
            for ref in refs:
                selected = _preferred_evidence_field(valid_refs[ref].get("fields", {}))
                if selected is None:
                    break
                field, actual = selected
                repaired_evidence.append({"ref": ref, "field": field, "value": str(actual)})
            if len(repaired_evidence) == len(refs):
                normalized = dict(claim)
                normalized["evidence"] = repaired_evidence
                accepted.append(normalized)
                repaired += 1
            else:
                rejected += 1
        else:
            rejected += 1
    return accepted, rejected, repaired


def _source_lines(results: list[dict], labels: dict, title: str = "数据来源") -> list[str]:
    lines = [f"## {title}"]
    for result in results:
        meta = result.get("source_meta", {})
        key = result.get("source_key", "")
        name = _plain_text(result.get("name") or meta.get("source_name") or key, 100)
        table = _plain_text(result.get("table_name", ""), 80)
        captured = meta.get("captured_at", "")
        url = meta.get("source_url", "")
        suffix = f"，分表：{table}" if table else ""
        time_text = f"，采集：{captured}" if captured else ""
        link = f"，[查看原文]({url})" if url else ""
        lines.append(
            f"- [{labels.get(key, key)}] {name}：{result.get('total', 0)} 条"
            f"{suffix}{time_text}{link}")
    return lines


def _fallback_claims(results: list[dict], labels: dict) -> list[dict]:
    """LLM 不可用时仍给记录级事实，不凭空宣称“整体正常”。"""
    claims = []
    for result in results:
        for item in result.get("detailed_items", [])[:3]:
            fields = [(k, v) for k, v in item.get("fields", {}).items() if v]
            if not fields:
                continue
            selected = _preferred_evidence_field(dict(fields))
            if selected is None:
                continue
            title_field, title_value = selected
            title = _plain_text(title_value, 180)
            details = "；".join(f"{_plain_text(k, 30)}：{_plain_text(v, 160)}"
                               for k, v in fields if k != title_field and
                               k not in {"类型", "分类", "序号", "编号"})
            evidence = item.get("evidence", {})
            ref = f"{result.get('source_key')}:{evidence.get('record_id')}"
            claims.append({
                "text": title + (f"（{details}）" if details else ""),
                "level": "info", "refs": [ref],
                "evidence": [{"ref": ref, "field": title_field,
                              "value": _plain_text(title_value, 160)}],
            })
    return claims[:12]


def _fallback_claims_from_units(units: list[dict]) -> list[dict]:
    """兜底也先写今日变更，再补充存量上下文。"""
    # 不能按输入顺序截前 12 条：大文件夹会吞掉全部配额，让后续数据源在兜底
    # 报告中完全消失。先按源保留各自优先级，再轮询取材，保证每个有内容的源都有
    # 至少一条可核对事实；总上限仍为 12，避免钉钉消息膨胀。
    by_source: dict[str, list[dict]] = {}
    for unit in units:
        source_key = str(unit.get("source_key") or "unknown")
        by_source.setdefault(source_key, []).append(unit)
    claims, offset = [], 0
    groups = list(by_source.values())
    while len(claims) < 12:
        added = False
        for group in groups:
            if offset >= len(group) or len(claims) >= 12:
                continue
            unit = group[offset]
            fields = [(k, v) for k, v in unit.get("fields", {}).items() if v]
            if fields:
                change = unit.get("change", {})
                kind = change.get("change")
                prefix = {"added": "新增：", "updated": "更新：", "removed": "移除："}.get(kind, "")
                selected = _preferred_evidence_field(dict(fields))
                if selected is None:
                    continue
                title_field, title_value = selected
                title = _plain_text(title_value, 180)
                details = "；".join(
                    f"{_plain_text(k, 30)}：{_plain_text(v, 160)}"
                    for k, v in fields if k != title_field and
                    k not in {"类型", "分类", "序号", "编号"})
                claims.append({
                    "text": prefix + title + (f"（{details}）" if details else ""),
                    "level": "update" if kind else "info", "refs": [unit["ref"]],
                    "evidence": [{"ref": unit["ref"], "field": title_field,
                                  "value": _plain_text(title_value, 160)}],
                })
                added = True
        if not added:
            break
        offset += 1
    return claims


def _claim_source_names(claim: dict, valid_refs: dict) -> list[str]:
    """一条结论涉及的来源名（按 refs 顺序去重）；来源不可考时返回 []。"""
    names = []
    for ref in claim.get("refs", []):
        evidence = (valid_refs.get(ref, {}) or {}).get("evidence", {}) or {}
        name = str(evidence.get("source_name") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _claim_lines(claims: list[dict], labels: dict, icons: dict) -> list[str]:
    """渲染一组结论的列表行（不含标题）；证据原值用句边界截断。"""
    lines = []
    for claim in claims[:12]:
        refs = " ".join(f"[{labels[r]}]" for r in claim["refs"] if r in labels)
        claim_text = _plain_text(claim.get("text", ""), 360)
        lines.append(f"- {icons.get(claim.get('level'), '•')} {claim_text} {refs}".rstrip())
        evidence_parts = []
        for evidence in claim.get("evidence", [])[:2]:
            value = _truncate_sentence(evidence.get("value", ""), 400)
            evidence_parts.append(f"{_plain_text(evidence.get('field'), 40)}：{value}")
        if evidence_parts:
            lines.append("  - 依据原值：" + "；".join(evidence_parts))
    return lines


def _render_claims_block(title: str, claims: list[dict], labels: dict, icons: dict,
                         levels: list[str] | None = None,
                         show_empty_fallback: bool = False,
                         valid_refs: dict | None = None,
                         cross_source_only: bool = False,
                         group_by_source: bool = False,
                         results: list[dict] | None = None) -> list[str]:
    """渲染一个结论区（按 levels 分流）。返回行列表；空区返回 []。

    v1.12.0：模板可配置多个结论区（如周报=进展 + 风险待决策）。
    无结论时只对第一个结论区显示兜底文案（避免多个空区重复提示）。
    v1.12.x：cross_source_only 只收跨来源聚合结论（总体结论先行）；
    group_by_source 按来源分块（各表最新总结 + 总体组）。
    """
    filtered = [c for c in claims if not levels or c.get("level") in levels]
    if not claims:
        if not show_empty_fallback:
            return []
        return [f"## {title}",
                "- 当前没有可由来源记录支持的结论，请查看来源数据。"]
    if not filtered:
        return []
    if cross_source_only or group_by_source:
        names_by_claim = [(_claim_source_names(c, valid_refs or {}), c)
                          for c in filtered]
        if cross_source_only:
            cross = [c for names, c in names_by_claim if len(names) > 1]
            if not cross:
                return []
            return [f"## {title}"] + _claim_lines(cross, labels, icons)
        # group_by_source：只渲染单来源结论、按数据源顺序分表；
        # 跨来源结论由「总体结论」区（cross_source_only）负责，不在此重复
        grouped: dict[str, list] = {}
        for names, claim in names_by_claim:
            if len(names) > 1:
                continue
            key = names[0] if names else "总体"
            grouped.setdefault(key, []).append(claim)
        lines = [f"## {title}"]
        if grouped.get("总体"):
            lines.append("### 📊 总体")
            lines.extend(_claim_lines(grouped["总体"], labels, icons))
        source_order = [str(r.get("name") or "") for r in (results or [])]
        for name in source_order:
            if grouped.get(name):
                lines.append(f"### {name}")
                lines.extend(_claim_lines(grouped[name], labels, icons))
        for name, group_claims in grouped.items():
            if name not in ("总体",) and name not in source_order:
                lines.append(f"### {name}")
                lines.extend(_claim_lines(group_claims, labels, icons))
        return lines
    return [f"## {title}"] + _claim_lines(filtered, labels, icons)


def _render(results: list[dict], claims: list[dict], labels: dict,
            title: str, date_str: str, headline: str = "",
            collection_errors: list[str] | None = None,
            spec: list[dict] | None = None,
            valid_refs: dict | None = None) -> str:
    """按 section_spec 渲染看板 Markdown。

    spec 为 [{kind, title, levels?, cross_source_only?, group_by_source?}]：
        headline — 顶部一句话要点（📌）
        claims   — 结论区（可多个；levels 限定收录级别；cross_source_only
                   只收跨来源聚合结论；group_by_source 按来源分块渲染）
        errors   — 数据完整性提醒（仅当 collection_errors 非空）
        sources  — 数据来源（原文链接）
    spec 缺省 = daily（_DEFAULT_SECTION_SPEC）。
    """
    icons = {"risk": "🔴", "decision": "🟠", "update": "🔵", "info": "•"}
    spec = spec or _DEFAULT_SECTION_SPEC
    blocks = [[f"# {title}"]]
    if date_str:
        blocks.append([f"> 数据日期：{date_str}"])
    total = sum(int(r.get("total", 0)) for r in results)
    default_headline = f"已核对 {len(results)} 个来源、{total} 条记录"
    rendered_headline = False
    rendered_claims = False
    first_claims = True
    for section in spec:
        if not isinstance(section, dict):
            continue
        kind = section.get("kind")
        if kind == "headline":
            blocks.append([f"📌 {section.get('title', '今日要点')}："
                           f"{headline or default_headline}"])
            rendered_headline = True
        elif kind == "claims":
            # 兜底文案只给第一个「常规」结论区（跨来源区空属正常，不占兜底位）
            show_fallback = first_claims and not section.get("cross_source_only")
            block = _render_claims_block(
                section.get("title", "重点更新"), claims, labels, icons,
                levels=section.get("levels"),
                show_empty_fallback=show_fallback,
                valid_refs=valid_refs,
                cross_source_only=bool(section.get("cross_source_only")),
                group_by_source=bool(section.get("group_by_source")),
                results=results)
            if not section.get("cross_source_only"):
                first_claims = False
            if block:
                blocks.append(block)
                rendered_claims = True
        elif kind == "errors":
            if collection_errors:
                blocks.append(
                    [f"## {section.get('title', '数据完整性提醒')}"] +
                    [f"- ⚠️ {_plain_text(e, 300)}" for e in collection_errors[:5]])
        elif kind == "sources":
            blocks.append(_source_lines(results, labels,
                                        section.get("title", "数据来源")))
    # 兜底：spec 没配 headline/claims 时仍保证报告不空
    if not rendered_headline:
        blocks.append([f"📌 今日要点：{headline or default_headline}"])
    if not rendered_claims:
        blocks.append(_render_claims_block(
            "重点更新", claims, labels, icons, show_empty_fallback=True))
    text = "\n\n".join("\n".join(block) for block in blocks).strip() + "\n"
    return text


def _paginate_markdown(text: str, title: str) -> list[str]:
    """按完整段落分页；不把半行或半个 Markdown 结构切到下一条消息。"""
    if len(text) <= REPORT_MARKDOWN_LEN:
        return [text]
    body = text
    heading = f"# {title}\n\n"
    if body.startswith(heading):
        body = body[len(heading):]
    blocks = [block.strip() for block in body.split("\n\n") if block.strip()]
    reserve = len(heading) + 40
    pages, current = [], []
    for block in blocks:
        # 正常渲染的单块已限制长度；仍异常超长时按完整行拆分。
        candidates = [block]
        if len(block) > REPORT_MARKDOWN_LEN - reserve:
            candidates = [line for line in block.splitlines() if line.strip()]
        for candidate in candidates:
            proposed = "\n\n".join(current + [candidate])
            if current and len(proposed) > REPORT_MARKDOWN_LEN - reserve:
                pages.append("\n\n".join(current))
                current = [candidate]
            else:
                current.append(candidate)
    if current:
        pages.append("\n\n".join(current))
    total = len(pages)
    return [f"# {title}\n\n> 第 {index}/{total} 页\n\n{page}\n"
            for index, page in enumerate(pages, 1)]


def build_dashboard_report(results: list[dict], old_snapshot: list[dict] | None,
                           llm_func: Callable | None, title: str = "恩特能源每日项目看板",
                           date_str: str = "", batch_size: int | None = None,
                           max_batch_chars: int | None = None,
                           collection_errors: list[str] | None = None,
                           template=None, task_prompt: str = "") -> DashboardReport:
    """完整数据分批提炼，再汇总并校验引用；任一步失败均可降级。

    v1.12.0：template 为 DashboardTemplate，控制输出格式（map/reduce 补充指令
    + section_spec + 标题覆盖）。缺省 None = daily 旧行为，逐字回归。
    """
    # 模板只改输出格式：map/reduce 指令 + 章节结构；采集/变化检测/证据校验不动。
    map_instruction = _DEFAULT_MAP_INSTRUCTION
    reduce_instruction = _DEFAULT_REDUCE_INSTRUCTION
    section_spec = None
    if template is not None:
        if getattr(template, "map_instructions", ""):
            map_instruction = template.map_instructions
        if getattr(template, "reduce_instructions", ""):
            reduce_instruction = template.reduce_instructions
        if getattr(template, "section_spec", None):
            section_spec = template.section_spec
        if getattr(template, "title", ""):
            title = template.title
    snapshot = make_snapshot(results)
    changes = field_diff(old_snapshot, snapshot)
    valid_refs, labels, records = _catalog(results)
    change_by_ref = {}
    removed_units = []
    deleted_index = 0
    for source_change in changes:
        source_key = source_change.get("source_key", "")
        for record_change in source_change.get("record_changes", []):
            record_id = str(record_change.get("record_id", ""))
            ref = f"{source_key}:{record_id}"
            change_by_ref[ref] = record_change
            if record_change.get("change") == "removed" and ref not in valid_refs:
                deleted_index += 1
                valid_refs[ref] = {
                    "evidence": record_change.get("evidence", {}),
                    "fields": record_change.get("fields", {}),
                }
                labels[ref] = f"{labels.get(source_key, source_key)}-D{deleted_index}"
                removed_units.append({
                    "ref": ref, "source": source_change.get("source_name", source_key),
                    "fields": record_change.get("fields", {}),
                    "evidence": record_change.get("evidence", {}),
                    "change": record_change,
                })
    analysis_units = []
    for record in records:
        unit = dict(record)
        if record["ref"] in change_by_ref:
            unit["change"] = change_by_ref[record["ref"]]
            unit["priority"] = "changed"
        else:
            unit["priority"] = "context"
        analysis_units.append(unit)
    for unit in removed_units:
        unit["priority"] = "changed"
    analysis_units.extend(removed_units)
    analysis_units.sort(key=lambda unit: 0 if unit.get("priority") == "changed" else 1)
    change_summary = []
    for source_change in changes:
        counts = {}
        if source_change.get("change"):
            counts[source_change["change"]] = 1
        for record_change in source_change.get("record_changes", []):
            kind = record_change.get("change", "updated")
            counts[kind] = counts.get(kind, 0) + 1
        change_summary.append({
            "source_key": source_change.get("source_key"),
            "source_name": source_change.get("source_name"),
            "counts": counts,
        })
    batch_size = batch_size or int(os.getenv("DASHBOARD_LLM_BATCH_SIZE", "250"))
    max_batch_chars = max_batch_chars or int(
        os.getenv("DASHBOARD_LLM_BATCH_CHARS", "220000"))
    selected_refs, rejected, repaired_evidence_claims, headline = [], 0, 0, ""
    map_completed = 0
    llm_errors = 0
    if llm_func:
        # v1.12.6（A3）：批预算从「全局对折」改为「每批独立超时」。
        # 原实现 as_completed(timeout=total*0.7)：单 worker 串行 + 全局 105s 预算，
        # 第 1 批慢调用吃光预算后剩余批被整批 cancel（实测 4 源 2 批只完成 1 批，内容截断）。
        # 现改为 worker 并行 + 每批各自 deadline：各批独立 LLM 调用，互不拖累，
        # 一批超时才丢该批（仍保留 accepted_claims 核验），不再「一刀切」丢全部剩余批。
        total_timeout = max(10, int(os.getenv("DASHBOARD_LLM_TOTAL_TIMEOUT", "150")))
        per_batch_timeout = max(30, int(os.getenv("DASHBOARD_LLM_BATCH_TIMEOUT", "120")))
        started = time.monotonic()
        batches = _chunks(analysis_units, max(1, batch_size),
                          max(2000, max_batch_chars))
        futures = {
            _MAP_EXECUTOR.submit(
                llm_func,
                _prompt("map", {
                    "records": batch,
                    "instruction": map_instruction,
                }, task_prompt),
                # 模型偶尔会无视“最多8个”而列出大量 ID；给足额度保证 JSON
                # 完整闭合，解析后 _selected_refs 仍只取前 8 个。
                max_tokens=10000,
            ): index
            for index, batch in enumerate(batches)
        }
        # 并行等待，逐批独立超时：completed 先完成先收，slow 批单独等满自己的
        # per_batch_timeout，避免单批慢拖垮整体预算导致其它批被连带跳过。
        for future in as_completed(futures):
            index = futures[future]
            try:
                value = _json_object(future.result(timeout=per_batch_timeout))
                selected_refs.extend(_selected_refs(value))
                map_completed += 1
            except FutureTimeout:
                future.cancel()
                llm_errors += 1
                logger.warning(
                    "看板 LLM 第 %s 批提炼超出单批预算(%ss)，跳过该批", index,
                    per_batch_timeout)
            except Exception as exc:
                llm_errors += 1
                logger.warning("看板 LLM 分批提炼失败(批%s): %s", index, exc)
        valid_selected = []
        seen_selected = set()
        for ref in selected_refs:
            if ref in valid_refs and ref not in seen_selected:
                valid_selected.append(ref)
                seen_selected.add(ref)
            elif ref not in valid_refs:
                rejected += 1
        if valid_selected:
            try:
                remaining = max(1, total_timeout - (time.monotonic() - started))
                unit_by_ref = {unit["ref"]: unit for unit in analysis_units}
                reduce_future = _REDUCE_EXECUTOR.submit(
                    llm_func, _prompt("reduce", {
                        "selected_records": [unit_by_ref[ref] for ref in valid_selected
                                             if ref in unit_by_ref],
                        "change_summary": change_summary,
                        "source_totals": [{
                            "source_key": r.get("source_key"), "name": r.get("name"),
                            "total": r.get("total"),
                            "status_counts": r.get("status_counts", {}),
                        } for r in results],
                        "instruction": reduce_instruction,
                    }, task_prompt), max_tokens=5000)
                reduced = _json_object(reduce_future.result(timeout=remaining))
                final_claims, reduce_rejected, repaired_evidence_claims = _verify(
                    _claims(reduced), valid_refs)
                rejected += reduce_rejected
            except FutureTimeout:
                llm_errors += 1
                reduce_future.cancel()
                logger.warning("看板 LLM 汇总超出整体时间预算，转来源化规则兜底")
                final_claims = []
            except Exception as exc:
                llm_errors += 1
                logger.warning("看板 LLM 汇总失败: %s", exc)
                final_claims = []
        else:
            final_claims = []
    else:
        final_claims = []
    verified_claim_count = len(final_claims)
    used_fallback = not final_claims
    if used_fallback:
        final_claims = _fallback_claims_from_units(analysis_units)
    # 总览由确定性变更统计生成，不采用无独立证据结构的自由文本。
    kind_counts: dict[str, int] = {}
    for change in change_by_ref.values():
        kind = change.get("change", "updated")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
    if kind_counts:
        parts = []
        for kind, label in (("added", "新增"), ("updated", "更新"), ("removed", "移除")):
            if kind_counts.get(kind):
                parts.append(f"{label} {kind_counts[kind]} 条")
        headline = "，".join(parts) + "；以下仅展示可回溯原值的重点"
    else:
        headline = "与上次快照相比暂无业务字段变化；以下为持续事项摘要"
    render_errors = list(collection_errors or []) + (
        [f"AI 整理完成 {map_completed}/{len(batches) if llm_func else 0} 个数据批次，"
         f"另有 {llm_errors} 个批次/阶段未完成；报告仅保留已通过原值核验的结论。"]
        if llm_errors else [])
    text = _render(results, final_claims, labels, title,
                   date_str or datetime.now().strftime("%Y-%m-%d"), headline,
                   render_errors, spec=section_spec, valid_refs=valid_refs)

    # 不能把“整份报告超过 5000 字”视为内容非法。逐源总结本来就可能超过单条
    # 钉钉消息的上限，正确行为是先按完整段落分页，再对每一页校验。此前这里在
    # 分页前做全量校验，导致已经通过 LLM+证据校验的 12 条结论被替换为前 4 条
    # 原始记录兜底（部门周报正是这个分支）。
    messages = _paginate_markdown(text, title)
    valid_messages = []
    invalid_reason = ""
    for message in messages:
        ok, reason = validate_markdown(message)
        if not ok and "表格语法" in reason:
            message = message.replace("|", "｜")
            ok, reason = validate_markdown(message)
        if not ok:
            invalid_reason = reason
            break
        valid_messages.append(message)

    # 只有单页内容本身仍无法发送时才启用规则兜底；不能因整份报告可分页而降级。
    if invalid_reason:
        logger.warning("看板分页后第 %s 页仍未通过通道校验（%s），规则兜底",
                       len(valid_messages) + 1, invalid_reason)
        used_fallback = True
        final_claims = _fallback_claims_from_units(analysis_units)[:4]
        text = _render(results, final_claims, labels,
                       title, date_str or datetime.now().strftime("%Y-%m-%d"),
                       "已生成来源可核验的规则兜底摘要", render_errors,
                       spec=section_spec, valid_refs=valid_refs)
        valid_messages = []
        for message in _paginate_markdown(text.replace("|", "｜"), title):
            ok, reason = validate_markdown(message)
            if not ok:
                # 这里不再以未经验证的任意文本替代；保留可控的通道级净化结果。
                logger.warning("规则兜底第 %s 页仍未通过校验：%s", len(valid_messages) + 1, reason)
                message = message[:MAX_MARKDOWN_LEN].rsplit("\n", 1)[0] + "\n"
            valid_messages.append(message)
    return DashboardReport(
        text=valid_messages[0], messages=valid_messages,
        snapshot=snapshot, changes=changes,
        verification={"accepted_claims": verified_claim_count,
                      "fallback_claims": len(final_claims) if used_fallback else 0,
                      "rejected_claims": rejected,
                      "repaired_evidence_claims": repaired_evidence_claims,
                      "llm_errors": llm_errors,
                      "map_batches": len(batches) if llm_func else 0,
                      "completed_map_batches": map_completed,
                      "message_pages": len(valid_messages)},
    )
