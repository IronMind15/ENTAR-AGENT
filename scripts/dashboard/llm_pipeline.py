"""来源可核验的看板 LLM 流水线。

LLM 负责从完整非敏感业务数据中提炼重点；代码只做分批、证据校验和钉钉渲染，
不再用固定板块/固定字段规则提前裁掉数据。
"""

from __future__ import annotations

import json
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
    max_workers=max(1, int(os.getenv("DASHBOARD_LLM_MAP_WORKERS", "1"))),
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
    text = str(value or "").replace("\r", "").strip()
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


def _prompt(stage: str, payload: dict) -> str:
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


def _verify(claims: list[dict], valid_refs: dict) -> tuple[list[dict], int]:
    accepted, rejected = [], 0
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
        else:
            rejected += 1
    return accepted, rejected


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
            title = _plain_text(fields[0][1], 120)
            details = "；".join(f"{_plain_text(k, 30)}：{_plain_text(v, 160)}"
                               for k, v in fields[1:4])
            evidence = item.get("evidence", {})
            ref = f"{result.get('source_key')}:{evidence.get('record_id')}"
            claims.append({
                "text": title + (f"（{details}）" if details else ""),
                "level": "info", "refs": [ref],
                "evidence": [{"ref": ref, "field": fields[0][0],
                              "value": _plain_text(fields[0][1], 160)}],
            })
    return claims[:12]


def _fallback_claims_from_units(units: list[dict]) -> list[dict]:
    """兜底也先写今日变更，再补充存量上下文。"""
    claims = []
    for unit in units:
        fields = [(k, v) for k, v in unit.get("fields", {}).items() if v]
        if not fields:
            continue
        change = unit.get("change", {})
        kind = change.get("change")
        prefix = {"added": "新增：", "updated": "更新：", "removed": "移除："}.get(kind, "")
        title = _plain_text(fields[0][1], 120)
        details = "；".join(f"{_plain_text(k, 30)}：{_plain_text(v, 160)}"
                           for k, v in fields[1:4])
        claims.append({
            "text": prefix + title + (f"（{details}）" if details else ""),
            "level": "update" if kind else "info", "refs": [unit["ref"]],
            "evidence": [{"ref": unit["ref"], "field": fields[0][0],
                          "value": _plain_text(fields[0][1], 160)}],
        })
    return claims[:12]


def _render_claims_block(title: str, claims: list[dict], labels: dict, icons: dict,
                         levels: list[str] | None = None,
                         show_empty_fallback: bool = False) -> list[str]:
    """渲染一个结论区（按 levels 分流）。返回行列表；空区返回 []。

    v1.12.0：模板可配置多个结论区（如周报=进展 + 风险待决策）。
    无结论时只对第一个结论区显示兜底文案（避免多个空区重复提示）。
    """
    filtered = [c for c in claims if not levels or c.get("level") in levels]
    if not claims:
        if not show_empty_fallback:
            return []
        return [f"## {title}",
                "- 当前没有可由来源记录支持的结论，请查看来源数据。"]
    if not filtered:
        return []
    lines = [f"## {title}"]
    for claim in filtered[:12]:
        refs = " ".join(f"[{labels[r]}]" for r in claim["refs"] if r in labels)
        claim_text = _plain_text(claim.get("text", ""), 360)
        lines.append(f"- {icons.get(claim.get('level'), '•')} {claim_text} {refs}".rstrip())
        evidence_parts = []
        for evidence in claim.get("evidence", [])[:2]:
            value = _plain_text(evidence.get("value", ""), 160)
            evidence_parts.append(f"{_plain_text(evidence.get('field'), 40)}：{value}")
        if evidence_parts:
            lines.append("  - 依据原值：" + "；".join(evidence_parts))
    return lines


def _render(results: list[dict], claims: list[dict], labels: dict,
            title: str, date_str: str, headline: str = "",
            collection_errors: list[str] | None = None,
            spec: list[dict] | None = None) -> str:
    """按 section_spec 渲染看板 Markdown。

    spec 为 [{kind, title, levels?}]：
        headline — 顶部一句话要点（📌）
        claims   — 结论区（可多个，levels 限定收录级别；无 levels 全收）
        errors   — 数据完整性提醒（仅当 collection_errors 非空）
        sources  — 数据来源
    spec 缺省 = daily（_DEFAULT_SECTION_SPEC），输出与旧硬编码逐字一致。
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
            block = _render_claims_block(
                section.get("title", "重点更新"), claims, labels, icons,
                levels=section.get("levels"),
                show_empty_fallback=first_claims)
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
                           template=None) -> DashboardReport:
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
    selected_refs, rejected, headline = [], 0, ""
    map_completed = 0
    llm_errors = 0
    if llm_func:
        total_timeout = max(10, int(os.getenv("DASHBOARD_LLM_TOTAL_TIMEOUT", "150")))
        started = time.monotonic()
        batches = _chunks(analysis_units, max(1, batch_size),
                          max(2000, max_batch_chars))
        futures = {
            _MAP_EXECUTOR.submit(
                llm_func,
                _prompt("map", {
                    "records": batch,
                    "instruction": map_instruction,
                }),
                # 模型偶尔会无视“最多8个”而列出大量 ID；给足额度保证 JSON
                # 完整闭合，解析后 _selected_refs 仍只取前 8 个。
                max_tokens=10000,
            ): index
            for index, batch in enumerate(batches)
        }
        try:
            completed = as_completed(futures, timeout=total_timeout * 0.7)
            for future in completed:
                try:
                    value = _json_object(future.result())
                    selected_refs.extend(_selected_refs(value))
                    map_completed += 1
                except Exception as exc:
                    llm_errors += 1
                    logger.warning("看板 LLM 分批提炼失败: %s", exc)
        except FutureTimeout:
            pending = [future for future in futures if not future.done()]
            llm_errors += len(pending)
            for future in pending:
                future.cancel()
            logger.warning("看板 LLM 分批提炼超出整体时间预算，跳过 %s 批", len(pending))
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
                    }), max_tokens=5000)
                reduced = _json_object(reduce_future.result(timeout=remaining))
                final_claims, reduce_rejected = _verify(_claims(reduced), valid_refs)
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
                   render_errors, spec=section_spec)
    if not validate_markdown(text)[0]:
        final_claims = _fallback_claims_from_units(analysis_units)[:4]
        text = _render(results, final_claims, labels,
                       title, date_str or datetime.now().strftime("%Y-%m-%d"),
                       "已生成来源可核验的规则兜底摘要", render_errors,
                       spec=section_spec)
    # 第二次仍不合法时做通道级净化；禁止未经校验的 fallback 直接外发。
    if not validate_markdown(text)[0]:
        text = text.replace("|", "｜")
    messages = _paginate_markdown(text, title)
    valid_messages = []
    for message in messages:
        if not validate_markdown(message)[0]:
            message = message.replace("|", "｜")
        valid_messages.append(message)
    return DashboardReport(
        text=valid_messages[0], messages=valid_messages,
        snapshot=snapshot, changes=changes,
        verification={"accepted_claims": verified_claim_count,
                      "fallback_claims": len(final_claims) if used_fallback else 0,
                      "rejected_claims": rejected, "llm_errors": llm_errors,
                      "map_batches": len(batches) if llm_func else 0,
                      "completed_map_batches": map_completed},
    )
