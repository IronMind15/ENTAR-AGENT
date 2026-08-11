"""
看板变化检测（v1.11.0）

changes_only 模式：只有数据相对上次推送有变化才推送。快照做「去噪」：
只保留 状态统计 + 关注/正常项的「状态 + 标题字段」，丢弃易变长文本，
避免「今天改了一句进展」就触发全量推送。
"""


def _normalize_items(items: list[dict]) -> list[dict]:
    """每条 item → {status, title}（状态 label 名不敏感，含「状态」即匹配）

    去噪：丢弃长文本字段，避免「改了一句进展」就触发全量推送；
    状态 / 名称（如 SN）变化才视为数据变化。
    """
    out = []
    for it in items or []:
        status, title = "", ""
        for k, v in it.items():
            if "状态" in k:
                status = v
            elif not title and v:
                title = v
        out.append({"status": status, "title": title})
    return out


def make_snapshot(results: list[dict]) -> list[dict]:
    """解析结果 → 可 JSON 序列化的去噪快照（alerts 用）"""
    snap = []
    for r in results:
        snap.append({
            "source_key": r["source_key"],
            "table_name": r.get("table_name", ""),
            "total": r["total"],
            "status_counts": r.get("status_counts", {}),
            "attention": _normalize_items(r.get("attention_items", [])),
            "normal": _normalize_items(r.get("normal_items", [])),
        })
    return snap


def has_changes(old_snap: list[dict] | None, new_snap: list[dict]) -> bool:
    """快照是否不同（None 视为首次无快照 → 有变化）"""
    return old_snap != new_snap


def _item_key(it: dict) -> str:
    return f"{it.get('status', '')}|{it.get('title', '')}"


def _diff_detail(old: dict, new: dict) -> str:
    parts = []
    if old.get("total") != new.get("total"):
        parts.append(f"总数 {old.get('total', 0)}→{new.get('total', 0)}")
    oc, nc = old.get("status_counts", {}), new.get("status_counts", {})
    for st, n in nc.items():
        if oc.get(st) != n:
            parts.append(f"{st}{oc.get(st, 0)}→{n}")
    for st in set(oc) - set(nc):
        parts.append(f"{st}清零")
    for old_set, new_set, label in (
        (old.get("attention", []), new.get("attention", []), "关注"),
        (old.get("normal", []), new.get("normal", []), "正常"),
    ):
        o, nn = {_item_key(x) for x in old_set}, {_item_key(x) for x in new_set}
        if nn - o:
            parts.append(f"新增{label}{len(nn - o)}")
        if o - nn:
            parts.append(f"移除{label}{len(o - nn)}")
    return "；".join(parts) if parts else "有变化"


def diff_summary(old_snap: list[dict] | None, new_snap: list[dict]) -> list[dict]:
    """两快照 → 变更摘要 [{source_key, detail}]；无变化返回空列表"""
    old_map = {s["source_key"]: s for s in old_snap or []}
    new_map = {s["source_key"]: s for s in new_snap or []}
    out: list[dict] = []
    for key, new in new_map.items():
        old = old_map.get(key)
        if old is None:
            out.append({"source_key": key, "detail": "新增板块"})
        elif old != new:
            out.append({"source_key": key, "detail": _diff_detail(old, new)})
    for key in old_map:
        if key not in new_map:
            out.append({"source_key": key, "detail": "板块消失"})
    return out


def change_banner(old_snap: list[dict] | None, new_snap: list[dict],
                  name_by_key: dict | None = None) -> str:
    """每日必推顶部变化标注（v1.11.4）

    有变化：`📌 今日变化：板块名：总数 2→3；新增关注1`
    无变化：`📌 今日无变化`
    name_by_key: {source_key: 板块名}，缺省时用 source_key 原样。
    """
    diff = diff_summary(old_snap, new_snap)
    if not diff:
        return "📌 今日无变化"
    parts = []
    for d in diff:
        name = (name_by_key or {}).get(d["source_key"], d["source_key"])
        parts.append(f"{name}：{d['detail']}")
    return "📌 今日变化：" + "；".join(parts)
