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


def _source_lines(results: list[dict], labels: dict) -> list[str]:
    lines = ["## 数据来源", ""]
    for result in results:
        meta = result.get("source_meta", {})
        key = result.get("source_key", "")
        name = result.get("name") or meta.get("source_name") or key
        table = result.get("table_name", "")
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
            title = str(fields[0][1])
            details = "；".join(f"{k}：{v}" for k, v in fields[1:4])
            evidence = item.get("evidence", {})
            ref = f"{result.get('source_key')}:{evidence.get('record_id')}"
            claims.append({
                "text": title + (f"（{details}）" if details else ""),
                "level": "info", "refs": [ref],
                "evidence": [{"ref": ref, "field": fields[0][0],
                              "value": str(fields[0][1])}],
            })
    return claims[:12]


def _render(results: list[dict], claims: list[dict], labels: dict,
            title: str, date_str: str, headline: str = "",
            collection_errors: list[str] | None = None) -> str:
    icons = {"risk": "🔴", "decision": "🟠", "update": "🔵", "info": "•"}
    lines = [f"# {title}", ""]
    if date_str:
        lines.extend([f"> 数据日期：{date_str}", ""])
    total = sum(int(r.get("total", 0)) for r in results)
    lines.extend([f"📌 今日要点：{headline or f'已核对 {len(results)} 个来源、{total} 条记录'}", ""])
    lines.extend(["## 重点更新", ""])
    if not claims:
        lines.append("- 当前没有可由来源记录支持的结论，请查看来源数据。")
    for claim in claims[:12]:
        refs = " ".join(f"[{labels[r]}]" for r in claim["refs"] if r in labels)
        lines.append(f"- {icons.get(claim.get('level'), '•')} {claim['text']} {refs}".rstrip())
        evidence_parts = []
        for evidence in claim.get("evidence", [])[:2]:
            value = str(evidence.get("value", ""))
            if len(value) > 100:
                value = value[:100] + "…"
            evidence_parts.append(f"{evidence.get('field')}：{value}")
        if evidence_parts:
            lines.append("  - 依据原值：" + "；".join(evidence_parts))
    if collection_errors:
        lines.extend(["", "## 数据完整性提醒", ""])
        lines.extend(f"- ⚠️ {error}" for error in collection_errors[:5])
    lines.extend([""] + _source_lines(results, labels))
    text = "\n".join(lines).strip() + "\n"
    if len(text) > REPORT_MARKDOWN_LEN:
        # 保住来源索引：先减少结论数量再渲染，而非生硬截断来源。
        if len(claims) > 4:
            return _render(results, claims[:-1], labels, title, date_str, headline,
                           collection_errors)
        text = text[:REPORT_MARKDOWN_LEN - 16].rsplit("\n", 1)[0] + "\n…（已截断）"
    return text


def build_dashboard_report(results: list[dict], old_snapshot: list[dict] | None,
                           llm_func: Callable | None, title: str = "恩特能源每日项目看板",
                           date_str: str = "", batch_size: int | None = None,
                           max_batch_chars: int | None = None,
                           collection_errors: list[str] | None = None) -> DashboardReport:
    """完整数据分批提炼，再汇总并校验引用；任一步失败均可降级。"""
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
        analysis_units.append(unit)
    analysis_units.extend(removed_units)
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
                    "instruction": "逐条阅读完整字段，只筛选本批最值得上报的记录引用ID",
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
                        "instruction": "依据筛选记录的完整字段去重、排序，生成最多12条老板摘要",
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
        final_claims = _fallback_claims(results, labels)
    # headline 没有独立证据结构，不采用 LLM 自由文本；总览由确定性统计生成。
    headline = ""
    render_errors = list(collection_errors or []) + (
        [f"AI 整理完成 {map_completed}/{len(batches) if llm_func else 0} 个数据批次，"
         f"另有 {llm_errors} 个批次/阶段未完成；报告仅保留已通过原值核验的结论。"]
        if llm_errors else [])
    text = _render(results, final_claims, labels, title,
                   date_str or datetime.now().strftime("%Y-%m-%d"), headline,
                   render_errors)
    if not validate_markdown(text)[0]:
        text = _render(results, _fallback_claims(results, labels)[:4], labels,
                       title, date_str, "已生成来源可核验的规则兜底摘要",
                       render_errors)
    return DashboardReport(
        text=text, snapshot=snapshot, changes=changes,
        verification={"accepted_claims": verified_claim_count,
                      "fallback_claims": len(final_claims) if used_fallback else 0,
                      "rejected_claims": rejected, "llm_errors": llm_errors,
                      "map_batches": len(batches) if llm_func else 0,
                      "completed_map_batches": map_completed},
    )
