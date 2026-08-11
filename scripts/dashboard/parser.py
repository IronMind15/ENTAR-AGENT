"""
看板数据解析层（v1.11.0）

纯函数，零外部依赖，可单测。参考方法论文档 §3.3/§3.4/§4。
"""

import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Optional

from .config_model import FieldSpec, SourceConfig

logger = logging.getLogger("dashboard.parser")

_SENSITIVE_LABEL_RE = re.compile(
    r"(password|passwd|secret|token|client.?secret|access.?key|密码|密钥|令牌|"
    r"身份证|银行卡|手机号)", re.IGNORECASE)


# ===== 单元格通用解析（方法论 §3.3） =====
def extract_cell_value(cell) -> str:
    """统一解析 AI表格单元格值，返回字符串

    处理类型：
    - None / 空 → ""
    - str → 直接返回
    - dict（单选/状态字段）→ 取 name 或 text
    - list[dict]（多选/多关联字段）→ 各项 name 用 " / " 连接
    """
    if not cell:
        return ""
    if isinstance(cell, str):
        return cell
    if isinstance(cell, dict):
        return str(cell.get("name", cell.get("text", str(cell))))
    if isinstance(cell, list):
        parts = []
        for item in cell:
            if isinstance(item, dict):
                parts.append(str(item.get("name", item.get("text", str(item)))))
            else:
                parts.append(str(item))
        return " / ".join(parts)
    return str(cell)


# ===== 周次分表自动检测（方法论 §3.4） =====
_WEEK_NUM_RE = re.compile(r"(\d+)")


def find_latest_week_table(tables: list[dict]) -> Optional[dict]:
    """从分表列表中找数字最大的周次表

    示例输入: [{"tableId":"a","tableName":"33周"}, {"tableId":"b","tableName":"32周"}]
    示例输出: {"tableId":"a","tableName":"33周"}
    """
    max_num, latest = 0, None
    for t in tables or []:
        if not isinstance(t, dict):
            continue
        # 兼容两种返回键：doc 接口的 tableName 与 list_sheets 标准化的 name
        name = t.get("tableName") or t.get("name") or ""
        match = _WEEK_NUM_RE.search(str(name))
        if match:
            n = int(match.group(1))
            if n > max_num:
                max_num, latest = n, t
    return latest


# ===== 数值格式化 =====
def format_number(v) -> str:
    """数值格式化：小数→百分比；#DIV/0!→无数据；其余原样"""
    if v is None or v == "":
        return ""
    if isinstance(v, str):
        s = v.strip()
        if "DIV/0" in s.upper():
            return "无数据"
        try:
            f = float(s)
        except ValueError:
            return s
    elif isinstance(v, (int, float)):
        f = float(v)
    else:
        return str(v)
    # 0~1 小数视为良率/比例 → 百分比
    if 0 <= f <= 1:
        return f"{f * 100:.0f}%"
    # 整数去掉小数点
    if f == int(f):
        return str(int(f))
    return f"{f:g}"


# ===== 记录解析 =====
def _find_status_field(source: SourceConfig) -> Optional[str]:
    """定位状态字段：field_map 中 label 含「状态」的 field_id"""
    for fid, spec in source.field_map.items():
        if "状态" in spec.label:
            return fid
    return None


def _find_status_field_for(field_map: dict) -> Optional[str]:
    """从 field_map dict 中找 label 含「状态」的字段（parse 内兜底用）"""
    for fid, spec in (field_map or {}).items():
        if "状态" in spec.label:
            return fid
    return None


def _infer_field_map(records: list[dict]) -> dict:
    """空 field_map 兜底：取首条记录 cells 键序，label 用 field_id

    动态数据源字段元数据（list_fields）未取到时的降级——看板照常出，
    列名显示为 field_id（如 01ZM8y7），状态分组退化为全部进 other_items。
    """
    out: dict = {}
    for rec in records or []:
        cells = rec.get("cells") or rec.get("fields") or rec
        if isinstance(cells, dict):
            for fid in cells:
                if fid not in out and isinstance(fid, str) \
                        and not fid.startswith("record"):
                    out[fid] = FieldSpec(label=fid, type="string")
        if out:
            break
    return out


def _extract_field(cells: dict, fid: str, spec: FieldSpec,
                   truncate: bool = True) -> str:
    """提取单个字段：按 type 解析 + 截断。

    records 的键可能是 field_id 或中文列名（钉钉 AI表格 records 实测用中文列名，
    而静态配置 field_map 用 field_id 索引），先按 fid 取，取不到按 spec.label 兜底。
    """
    raw = cells.get(fid)
    if raw is None and spec.label and spec.label != fid:
        raw = cells.get(spec.label)
    if spec.type == "percent":
        return format_number(raw)
    value = extract_cell_value(raw)
    max_len = spec.max_len or 200
    if truncate and len(value) > max_len:
        value = value[:max_len] + "…"
    return value


def _is_sensitive_label(label: str) -> bool:
    """仅拦截凭证和高风险个人标识；普通业务字段完整交给 LLM。"""
    return bool(_SENSITIVE_LABEL_RE.search(label or ""))


def _source_url(source: SourceConfig) -> str:
    if source.source_url:
        return source.source_url
    if source.base_id:
        return f"https://alidocs.dingtalk.com/i/nodes/{source.base_id}"
    return ""


def _record_identity(source: SourceConfig, rec: dict, fields: dict,
                     row_number: int) -> tuple[str, str]:
    """优先使用平台记录 ID；否则用业务首字段生成跨排序稳定键。"""
    for key in ("recordId", "record_id", "id"):
        value = rec.get(key)
        if value not in (None, ""):
            record_id = str(value)
            return record_id, record_id
    business_value = next((str(v) for k, v in fields.items()
                           if v and "状态" not in k), "")
    seed = json.dumps([source.key, business_value or fields], ensure_ascii=False,
                      sort_keys=True, default=str)
    stable_key = "auto-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return stable_key, stable_key if business_value else f"{stable_key}-{row_number}"


def parse_source_records(source: SourceConfig, records: list[dict],
                         table_name: str = "") -> dict:
    """把 AI表格原始记录解析为看板业务结构

    Args:
        source: 数据源配置
        records: 原始记录列表（每条含 cells/fields 字典）
        table_name: 当前分表名（latest_week 模式显示周次用）

    Returns:
        {"source_key", "name", "table_name", "total", "items",
         "status_counts", "attention_items", "normal_items", "other_items"}
    """
    # 空 field_map 兜底：动态源字段名未取到 → 用首条记录 cells 键序
    field_map = source.field_map or _infer_field_map(records)

    status_fid = _find_status_field_for(field_map)
    status_label = field_map[status_fid].label if status_fid else ""
    attention_set = set(source.status_groups.get("attention", []) or [])
    normal_set = set(source.status_groups.get("normal", []) or [])

    items: list[dict] = []
    status_counts: dict[str, int] = {}
    attention_items: list[dict] = []
    normal_items: list[dict] = []
    other_items: list[dict] = []

    captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
    detailed_items: list[dict] = []
    identity_counts: dict[str, int] = {}
    for row_number, rec in enumerate(records or [], 1):
        if not isinstance(rec, dict):
            continue
        cells = rec.get("cells") or rec.get("fields") or rec
        if not isinstance(cells, dict):
            continue
        item: dict = {}
        detailed_fields: dict = {}
        for fid, spec in field_map.items():
            if _is_sensitive_label(spec.label):
                continue
            item[spec.label] = _extract_field(cells, fid, spec)
            detailed_fields[spec.label] = _extract_field(
                cells, fid, spec, truncate=False)
        record_id, record_key = _record_identity(
            source, rec, detailed_fields, row_number)
        identity_counts[record_key] = identity_counts.get(record_key, 0) + 1
        if identity_counts[record_key] > 1:
            suffix = identity_counts[record_key]
            record_id = f"{record_id}-{suffix}"
            record_key = f"{record_key}-{suffix}"
        evidence = {
            "source_key": source.key,
            "source_name": source.name,
            "source_kind": source.kind,
            "source_url": _source_url(source),
            "node_id": source.base_id,
            "sheet_id": source.table_id,
            "table_name": table_name or "",
            "record_id": record_id,
            "record_key": record_key,
            "row_number": row_number,
            "captured_at": captured_at,
        }
        detailed_items.append({"fields": detailed_fields, "evidence": evidence})
        # 状态统计与分组
        status_value = item.get(status_label, "") if status_label else ""
        if status_label:
            status_counts[status_value] = status_counts.get(status_value, 0) + 1
        if status_value in attention_set:
            attention_items.append(item)
        elif status_value in normal_set:
            normal_items.append(item)
        else:
            other_items.append(item)
        items.append(item)

    return {
        "source_key": source.key,
        "name": source.name,
        "table_name": table_name or "",
        "total": len(items),
        "items": items,
        "detailed_items": detailed_items,
        "source_meta": {
            "source_key": source.key, "source_name": source.name,
            "source_kind": source.kind, "source_url": _source_url(source),
            "node_id": source.base_id, "sheet_id": source.table_id,
            "table_name": table_name or "", "captured_at": captured_at,
        },
        "status_counts": status_counts,
        "attention_items": attention_items,
        "normal_items": normal_items,
        "other_items": other_items,
    }
