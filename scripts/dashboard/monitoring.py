"""文档监测语义层。

看板原本主要回答“今天要不要推一份报告”。这里把一次检查提升为
“文档相对上次检查发生了什么、结果是否完整、是否建立了基线”的明确状态，
供定时器、即时查询和推送共用。只做确定性判断，不让 LLM 决定运行状态。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .alerts import diff_summary


@dataclass(frozen=True)
class MonitorOutcome:
    """一次文档检查的可解释结果。"""

    status: str  # baseline | changed | unchanged | partial | failed
    label: str
    reason: str = ""
    changes: list[dict] = field(default_factory=list)


def evaluate(old_snapshot: list[dict] | None,
             new_snapshot: list[dict] | None,
             errors: list[str] | None = None) -> MonitorOutcome:
    """根据快照和采集错误判定检查状态。

    ``errors`` 与有效数据并存时是 partial，而不是把整次检查伪装成成功；
    没有任何有效数据时是 failed。首次有数据则是 baseline，避免把初次
    登记误报为“文档新增了大量内容”。
    """
    errors = [str(e) for e in (errors or []) if str(e).strip()]
    new_snapshot = new_snapshot or []
    if not new_snapshot:
        return MonitorOutcome("failed", "检查失败", "；".join(errors) or "没有采集到可用内容")
    changes = diff_summary(old_snapshot, new_snapshot)
    if old_snapshot is None:
        status, label = "baseline", "首次检查，已建立基线"
    elif changes:
        status, label = "changed", "发现内容变化"
    else:
        status, label = "unchanged", "未发现重要变化"
    if errors:
        status = "partial"
        label = "部分内容检查成功"
    return MonitorOutcome(status, label, "；".join(errors), changes)


def render_banner(outcome: MonitorOutcome,
                  name_by_key: dict[str, str] | None = None) -> str:
    """渲染推送首屏的监测结论，避免只显示笼统的“今日变化”。"""
    lines = [f"📡 检查结果：{outcome.label}"]
    if outcome.changes:
        parts = []
        for item in outcome.changes:
            key = item.get("source_key", "")
            name = (name_by_key or {}).get(key, item.get("source_name", key))
            detail = item.get("detail", "有变化")
            if item.get("change") == "baseline":
                detail = "已记录为首次基线"
            parts.append(f"{name}：{detail}")
        lines.append("📝 变化摘要：" + "；".join(parts))
    if outcome.reason:
        lines.append("⚠️ 完整性提醒：" + outcome.reason)
    return "\n".join(lines)
