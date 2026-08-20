"""
组织架构中心配置

定义恩特能源各中心/部门的编码和名称映射。
后续可在每个中心下扩展子部门（department）列表。

用法：
    from scripts.center_config import CENTER_MAP, get_center_name, is_valid_center

当前中心（2026-07-22 设定，五中心 + 公共区）：
  - pmo   : 产品与项目管理中心（PMO）
  - rd    : 研发中心
  - mfg   : 制造中心
  - bz    : 商业中心
  - ops   : 运营支持中心
  - public: 全公司公开（默认）
"""

CENTER_MAP = {
    # id       display_name                   简称
    "public": ("全公司公开",                    "公共"),
    "pmo":    ("产品与项目管理中心",            "PMO"),
    "rd":     ("研发中心",                      "研发"),
    "mfg":    ("制造中心",                      "制造"),
    "bz":     ("商业中心",                      "商业"),
    "ops":    ("运营支持中心",                  "运营"),
}

# 有序列表，用于 UI 下拉框等场景（public 放第一个）
CENTER_IDS = ["public", "pmo", "rd", "mfg", "bz", "ops"]

# 审核命令中可识别的别名（显示名和简称均可）
_CENTER_ALIASES: dict[str, str] = {}
for cid, (display, short) in CENTER_MAP.items():
    _CENTER_ALIASES[display] = cid
    _CENTER_ALIASES[short] = cid
    # 再加小写版本
    _CENTER_ALIASES[display.lower()] = cid
    _CENTER_ALIASES[short.lower()] = cid


def get_center_name(center_id: str) -> str:
    """根据中心 ID 获取完整显示名"""
    return CENTER_MAP.get(center_id, (center_id,))[0]


def get_center_short(center_id: str) -> str:
    """根据中心 ID 获取简称"""
    pair = CENTER_MAP.get(center_id)
    return pair[1] if pair and len(pair) > 1 else center_id


def resolve_center(input_str: str) -> str | None:
    """将用户输入的文本（显示名/简称/ID）解析为中心 ID

    支持：
      - "研发中心" → "rd"
      - "研发"     → "rd"
      - "rd"       → "rd"
      - "公共"     → "public"
    不匹配时返回 None。
    """
    raw = input_str.strip()
    # 直接匹配 ID
    if raw in CENTER_MAP:
        return raw
    # 匹配别名
    return _CENTER_ALIASES.get(raw) or _CENTER_ALIASES.get(raw.lower())


def is_valid_center(center_id: str) -> bool:
    """判断是否是合法的中心 ID"""
    return center_id in CENTER_MAP


def list_centers() -> list[dict]:
    """返回中心列表，供 API/UI 使用"""
    return [
        {"id": cid, "name": get_center_name(cid), "short": get_center_short(cid)}
        for cid in CENTER_IDS
    ]
