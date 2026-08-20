"""
看板数据源配置（v1.11.0）

config.py 的 `_read_from_file` 只支持单行 `KEY = 值`，无法承载嵌套的
field_map / status_groups 结构，因此数据源定义放在独立 JSON 文件
`scripts/dashboard/dashboard_sources.json`（stdlib json 解析，无新依赖）。

加载三层：内置默认 → JSON 文件 → env/local_config 覆盖（推送标量）。
"""

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("dashboard.config")

# JSON 配置文件路径（可被环境变量 DASHBOARD_SOURCES_FILE 覆盖）
_JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "dashboard_sources.json")

# 字段取值类型
_KNOWN_TYPES = ("string", "dict_name", "list_name", "list_text", "percent")


@dataclass
class FieldSpec:
    """字段映射项：field_id → 业务含义"""
    label: str                          # 业务标签（看板展示用）
    type: str = "string"                # string | dict_name | list_name | list_text | percent
    max_len: int = 200                  # 展示截断长度


@dataclass
class SourceConfig:
    """单个数据源配置"""
    key: str                            # 配置里的唯一 key（如 project_status）
    name: str = ""                      # 展示名（板块标题）
    source: str = "dingtalk_doc"        # dingtalk_doc | xlsx_file（v1.11 只做 dingtalk_doc）
    kind: str = "notable"               # notable | workbook | doc
    base_id: str = ""                   # AI表格 Base ID（= 文档 nodeId）
    source_url: str = ""                # 原始钉钉文档地址（报告来源索引用）
    table_mode: str = "fixed"           # fixed | latest_week（按周分表自动取最新）
    table_id: str = ""                  # fixed 模式的 sheetId
    field_map: dict[str, FieldSpec] = field(default_factory=dict)  # field_id → FieldSpec
    status_groups: dict[str, list[str]] = field(default_factory=dict)  # 状态分组
    enabled: bool = True
    operator_id: str = ""          # 读文档身份（动态源=登记人 unionId；跨用户订阅用）


@dataclass
class PushConfig:
    """推送配置（标量，来自 config.py / local_config.py）"""
    push_hour: int = 9
    push_minute: int = 0
    alert_mode: str = "always"    # always（每日必推）| changes_only | off
    title: str = "恩特能源每日项目看板"
    weekdays: str = ""                  # ""=每天；"1,5"=周一、周五（0-6）


# ===== 加载与缓存（mtime 感知） =====
_lock = threading.Lock()
_cached_sources: Optional[list[SourceConfig]] = None
_cached_mtime: float = -1.0


def _builtin_sources() -> list[SourceConfig]:
    """内置默认数据源（JSON 缺失/损坏时的兜底；生产数据源由 JSON 提供）"""
    return []


def _parse_sources(data: dict) -> list[SourceConfig]:
    """解析 JSON 字典 → SourceConfig 列表（跳过字段非法项，不中断整体）"""
    out: list[SourceConfig] = []
    raw_sources = data.get("sources", {}) if isinstance(data, dict) else {}
    if not isinstance(raw_sources, dict):
        logger.warning("看板配置 sources 不是对象，忽略")
        return out
    for key, raw in raw_sources.items():
        if not isinstance(raw, dict):
            continue
        field_map: dict[str, FieldSpec] = {}
        raw_map = raw.get("field_map", {})
        if isinstance(raw_map, dict):
            for fid, spec in raw_map.items():
                if not isinstance(spec, dict):
                    continue
                ftype = str(spec.get("type", "string"))
                if ftype not in _KNOWN_TYPES:
                    ftype = "string"
                field_map[str(fid)] = FieldSpec(
                    label=str(spec.get("label", fid)),
                    type=ftype,
                    max_len=int(spec.get("max_len", 200)),
                )
        status_groups = raw.get("status_groups", {})
        if not isinstance(status_groups, dict):
            status_groups = {}
        cfg = SourceConfig(
            key=str(key),
            name=str(raw.get("name", key)),
            source=str(raw.get("source", "dingtalk_doc")),
            kind=str(raw.get("kind", "notable")),
            base_id=str(raw.get("base_id", "")),
            source_url=str(raw.get("source_url", "")),
            table_mode=str(raw.get("table_mode", "fixed")),
            table_id=str(raw.get("table_id", "")),
            field_map=field_map,
            status_groups={
                str(g): [str(v) for v in vals] if isinstance(vals, list) else []
                for g, vals in status_groups.items()
            },
            enabled=bool(raw.get("enabled", True)),
        )
        out.append(cfg)
    return out


def load_sources() -> list[SourceConfig]:
    """加载数据源配置（带 mtime 缓存 + 坏 JSON 容错）"""
    global _cached_sources, _cached_mtime
    try:
        mtime = os.path.getmtime(_JSON_PATH)
    except OSError:
        mtime = -1.0
    with _lock:
        if _cached_sources is not None and mtime == _cached_mtime:
            return list(_cached_sources)
        if mtime >= 0:
            try:
                with open(_JSON_PATH, encoding="utf-8") as f:
                    data = json.load(f)
                sources = _parse_sources(data)
                _cached_sources = sources
                _cached_mtime = mtime
                return list(sources)
            except Exception as e:
                logger.warning(f"看板数据源配置解析失败，使用内置默认: {e}")
        sources = _builtin_sources()
        _cached_sources = sources
        _cached_mtime = mtime
        return list(sources)


def get_source(key: str) -> Optional[SourceConfig]:
    """按 key 取数据源"""
    for s in load_sources():
        if s.key == key:
            return s
    return None


def source_usable(src: Optional[SourceConfig]) -> bool:
    """数据源是否可用于实际采集（v1.11.5）

    钉钉文档源必须有 base_id（AI表格/在线表格的 Base ID = 文档 nodeId）。
    静态配置里 base_id 为空的历史残留（如 project_status/test_issues）会导致
    /v1.0/notable/bases//sheets 404，此处运行期过滤 + 告警。
    """
    if src is None or not src.enabled:
        return False
    if src.source == "dingtalk_doc" and not (src.base_id or "").strip():
        logger.warning(f"数据源 {src.key} 未配置 base_id，跳过（请用钉钉文档登记数据源）")
        return False
    return True


def load_push_config() -> PushConfig:
    """加载推送配置（标量走 config.py / local_config.py）"""
    try:
        from scripts.config import (
            DASHBOARD_PUSH_HOUR, DASHBOARD_PUSH_MINUTE,
            DASHBOARD_ALERT_MODE, DASHBOARD_TITLE,
        )
        push_hour = int(DASHBOARD_PUSH_HOUR)
        push_minute = int(DASHBOARD_PUSH_MINUTE)
    except Exception:
        push_hour, push_minute = 9, 0
    try:
        from scripts.config import DASHBOARD_WEEKDAYS
        weekdays = DASHBOARD_WEEKDAYS
    except Exception:
        weekdays = ""
    try:
        alert_mode = DASHBOARD_ALERT_MODE
    except Exception:
        alert_mode = "always"
    try:
        title = DASHBOARD_TITLE
    except Exception:
        title = "恩特能源每日项目看板"
    if alert_mode not in ("always", "changes_only", "off"):
        alert_mode = "always"
    return PushConfig(
        push_hour=push_hour, push_minute=push_minute,
        alert_mode=alert_mode, title=title, weekdays=weekdays,
    )
