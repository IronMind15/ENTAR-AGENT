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
    """解析结果 → 完整业务快照。

    旧的 attention/normal 字段继续保留以兼容历史快照；新增 records 保存
    每条记录的完整非敏感字段，确保进展、风险、负责人等变化不会被吞掉。
    """
    snap = []
    for r in results:
        source_meta = {
            k: v for k, v in (r.get("source_meta", {}) or {}).items()
            if k != "captured_at"
        }
        source_snap = {
            "source_key": r["source_key"],
            "table_name": r.get("table_name", ""),
            "total": r["total"],
            "status_counts": r.get("status_counts", {}),
            "attention": _normalize_items(r.get("attention_items", [])),
            "normal": _normalize_items(r.get("normal_items", [])),
            # captured_at 是采集元数据，不是业务变化；否则每次轮询都会误判。
        }
        # 老调用方没有 detailed_items 时不强塞空字段，保证历史快照可直接比较。
        if source_meta:
            source_snap["source_meta"] = source_meta
        if "detailed_items" in r:
            source_snap["records"] = [
                {
                    "record_id": x.get("evidence", {}).get("record_id", ""),
                    "record_key": x.get("evidence", {}).get("record_key", ""),
                    "fields": x.get("fields", {}),
                    "evidence": {
                        k: v for k, v in (x.get("evidence", {}) or {}).items()
                        if k != "captured_at"
                    },
                }
                for x in r.get("detailed_items", [])
            ]
        snap.append(source_snap)
    return snap


def _records_by_key(source_snap: dict) -> dict:
    out = {}
    for rec in source_snap.get("records", []) or []:
        key = rec.get("record_key") or rec.get("record_id")
        if key:
            out[str(key)] = rec
    return out


def field_diff(old_snap: list[dict] | None,
               new_snap: list[dict]) -> list[dict]:
    """返回记录/字段级变化，并保留可回溯来源。

    兼容没有 ``records`` 的旧快照：首次升级标记为快照迁移而非业务新增，之后
    即可稳定比较。返回值可直接作为 LLM 的“今日变化”证据输入。
    """
    old_map = {s.get("source_key"): s for s in old_snap or []}
    new_map = {s.get("source_key"): s for s in new_snap or []}
    changes = []
    if old_snap is None:
        return [{
            "source_key": key,
            "source_name": source.get("source_meta", {}).get("source_name", key),
            "change": "baseline", "record_changes": [],
        } for key, source in new_map.items()]
    for source_key, new_source in new_map.items():
        old_source = old_map.get(source_key)
        if old_source is None:
            changes.append({
                "source_key": source_key,
                "source_name": new_source.get("source_meta", {}).get(
                    "source_name", source_key),
                "change": "source_added", "record_changes": [],
            })
            continue
        if "records" not in old_source:
            changes.append({
                "source_key": source_key,
                "source_name": new_source.get("source_meta", {}).get(
                    "source_name", source_key),
                "change": "snapshot_upgraded", "record_changes": [],
            })
            continue
        old_records = _records_by_key(old_source or {})
        new_records = _records_by_key(new_source)
        record_changes = []
        for key, new_rec in new_records.items():
            evidence = new_rec.get("evidence", {})
            old_rec = old_records.get(key)
            if old_rec is None:
                record_changes.append({
                    "change": "added", "source_key": source_key,
                    "record_id": new_rec.get("record_id", ""),
                    "fields": new_rec.get("fields", {}), "evidence": evidence,
                })
                continue
            before, after = old_rec.get("fields", {}), new_rec.get("fields", {})
            changed_fields = {}
            for field_name in sorted(set(before) | set(after)):
                if before.get(field_name) != after.get(field_name):
                    changed_fields[field_name] = {
                        "before": before.get(field_name, ""),
                        "after": after.get(field_name, ""),
                    }
            if changed_fields:
                record_changes.append({
                    "change": "updated", "source_key": source_key,
                    "record_id": new_rec.get("record_id", ""),
                    "changed_fields": changed_fields, "evidence": evidence,
                })
        for key, old_rec in old_records.items():
            if key not in new_records:
                evidence = old_rec.get("evidence", {})
                record_changes.append({
                    "change": "removed", "source_key": source_key,
                    "record_id": old_rec.get("record_id", ""),
                    "fields": old_rec.get("fields", {}), "evidence": evidence,
                })
        if record_changes:
            changes.append({
                "source_key": source_key,
                "source_name": new_source.get("source_meta", {}).get(
                    "source_name", source_key),
                "record_changes": record_changes,
            })
    for source_key, old_source in old_map.items():
        if source_key not in new_map:
            changes.append({
                "source_key": source_key,
                "source_name": old_source.get("source_meta", {}).get(
                    "source_name", source_key),
                "change": "source_removed", "record_changes": [],
            })
    return changes


def has_changes(old_snap: list[dict] | None, new_snap: list[dict]) -> bool:
    """快照是否不同；旧版快照首次升级时按旧字段比较，避免迁移误报。"""
    if old_snap is None:
        return True
    old_map = {s.get("source_key"): s for s in old_snap}
    new_map = {s.get("source_key"): s for s in new_snap}
    if set(old_map) != set(new_map):
        return True
    for key, old in old_map.items():
        new = dict(new_map[key])
        if "records" not in old:
            new.pop("records", None)
            new.pop("source_meta", None)
        if old != new:
            return True
    return False


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
    if "records" in old and old.get("records") != new.get("records"):
        old_records, new_records = _records_by_key(old), _records_by_key(new)
        added = len(set(new_records) - set(old_records))
        removed = len(set(old_records) - set(new_records))
        updated = sum(
            old_records[k].get("fields", {}) != new_records[k].get("fields", {})
            for k in set(old_records) & set(new_records))
        if added:
            parts.append(f"新增记录{added}")
        if updated:
            parts.append(f"字段更新{updated}")
        if removed:
            parts.append(f"移除记录{removed}")
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
        elif has_changes([old], [new]):
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
    banner = "📌 今日变化：" + "；".join(parts)
    return banner if len(banner) <= 350 else banner[:347] + "…"
